# Lambda container image (Function URL + Mangum). No VPC, no NAT: the function
# reaches CockroachDB Cloud over public TLS and Bedrock over the AWS API.
FROM public.ecr.aws/lambda/python:3.12

COPY pyproject.toml README.md ${LAMBDA_TASK_ROOT}/
COPY src/ ${LAMBDA_TASK_ROOT}/src/
COPY web/ ${LAMBDA_TASK_ROOT}/web/

RUN pip install --no-cache-dir ${LAMBDA_TASK_ROOT}/

CMD ["dejaops.api.handler"]
