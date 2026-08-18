#!/usr/bin/env bash
# Deploy DejaOps to AWS Lambda (container image + Function URL).
#
# Cost posture: no VPC (no NAT Gateway), no always-on compute. Secrets live in
# SSM Parameter Store and are injected as Lambda env vars at deploy time.
#
# Prereqs: aws cli configured, docker, Bedrock model access enabled in $REGION.
# One-time secret setup:
#   aws ssm put-parameter --name /dejaops/database-url --type SecureString --value 'postgresql://...'
#   aws ssm put-parameter --name /dejaops/demo-token   --type SecureString --value 'pick-a-token'

set -euo pipefail

REGION="${AWS_REGION:-ap-south-1}"
FUNC="dejaops"
REPO="dejaops"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
IMAGE="${ECR}/${REPO}:latest"

echo "==> Build & push image"
aws ecr describe-repositories --repository-names "$REPO" --region "$REGION" >/dev/null 2>&1 \
  || aws ecr create-repository --repository-name "$REPO" --region "$REGION" >/dev/null
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$ECR"
docker build -t "$IMAGE" .
docker push "$IMAGE"

echo "==> Read secrets from SSM"
ssm() { aws ssm get-parameter --name "$1" --with-decryption --query Parameter.Value --output text --region "$REGION" 2>/dev/null; }
DATABASE_URL=$(ssm /dejaops/database-url)
DEMO_TOKEN=$(ssm /dejaops/demo-token)
ANTHROPIC_API_KEY=$(ssm /dejaops/anthropic-api-key)
VOYAGE_API_KEY=$(ssm /dejaops/voyage-api-key)

# Provider routing (see src/dejaops/config.py):
#   PROVIDER=bedrock   Claude + Titan via Amazon Bedrock  (default; needs model access)
#   PROVIDER=direct    Claude API + Voyage AI             (fallback while Bedrock is pending)
#   FAKE=1             offline stand-ins, no vendor calls at all
ENV_JSON=$(mktemp)
trap 'rm -f "$ENV_JSON"' EXIT
python3 - "$DATABASE_URL" "$DEMO_TOKEN" "$ANTHROPIC_API_KEY" "$VOYAGE_API_KEY" \
         "${PROVIDER:-bedrock}" "${FAKE:-0}" > "$ENV_JSON" <<'PY'
import json, sys
db, token, anthropic_key, voyage_key, provider, fake = sys.argv[1:7]
env = {"DATABASE_URL": db, "DEMO_TOKEN": token, "MAX_AGENT_ITERATIONS": "5"}
if provider == "direct":
    env |= {
        "LLM_PROVIDER": "anthropic",
        "EMBED_PROVIDER": "voyage",
        "ANTHROPIC_API_KEY": anthropic_key,
        "VOYAGE_API_KEY": voyage_key,
    }
else:
    env |= {"LLM_PROVIDER": "bedrock", "EMBED_PROVIDER": "bedrock"}
if fake == "1":
    env |= {"FAKE_LLM": "1", "FAKE_EMBEDDINGS": "1"}
print(json.dumps({"Variables": env}))
PY
ENV_VARS="file://${ENV_JSON}"

echo "==> Create or update function"
if aws lambda get-function --function-name "$FUNC" --region "$REGION" >/dev/null 2>&1; then
  aws lambda update-function-code --function-name "$FUNC" --image-uri "$IMAGE" --region "$REGION" >/dev/null
  aws lambda wait function-updated --function-name "$FUNC" --region "$REGION"
  aws lambda update-function-configuration --function-name "$FUNC" \
    --environment "$ENV_VARS" --timeout 120 --memory-size 512 --region "$REGION" >/dev/null
else
  ROLE="dejaops-lambda-role"
  aws iam get-role --role-name "$ROLE" >/dev/null 2>&1 || {
    aws iam create-role --role-name "$ROLE" --assume-role-policy-document '{
      "Version":"2012-10-17",
      "Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
    aws iam attach-role-policy --role-name "$ROLE" \
      --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
    # Bedrock invoke, scoped to the two models we use
    aws iam put-role-policy --role-name "$ROLE" --policy-name bedrock-invoke --policy-document '{
      "Version":"2012-10-17",
      "Statement":[{"Effect":"Allow","Action":["bedrock:InvokeModel"],"Resource":"*"}]}'
    sleep 10  # IAM propagation
  }
  aws lambda create-function --function-name "$FUNC" \
    --package-type Image --code ImageUri="$IMAGE" \
    --role "arn:aws:iam::${ACCOUNT_ID}:role/${ROLE}" \
    --environment "$ENV_VARS" --timeout 120 --memory-size 512 --region "$REGION" >/dev/null
  aws lambda wait function-active --function-name "$FUNC" --region "$REGION"
fi

echo "==> Ensure public Function URL"
aws lambda get-function-url-config --function-name "$FUNC" --region "$REGION" >/dev/null 2>&1 || {
  aws lambda create-function-url-config --function-name "$FUNC" --auth-type NONE --region "$REGION" >/dev/null
  aws lambda add-permission --function-name "$FUNC" --statement-id url-public \
    --action lambda:InvokeFunctionUrl --principal '*' --function-url-auth-type NONE --region "$REGION" >/dev/null
}
URL=$(aws lambda get-function-url-config --function-name "$FUNC" --region "$REGION" --query FunctionUrl --output text)
echo "==> Deployed: ${URL}"
echo "    Demo access: ${URL}?token=<DEMO_TOKEN>"
