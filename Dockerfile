FROM public.ecr.aws/lambda/python:3.12

COPY pyproject.toml README.md ${LAMBDA_TASK_ROOT}/
COPY src/ ${LAMBDA_TASK_ROOT}/src/
COPY web/ ${LAMBDA_TASK_ROOT}/web/
COPY certs/ ${LAMBDA_TASK_ROOT}/certs/

RUN pip install --no-cache-dir ${LAMBDA_TASK_ROOT}/

CMD ["dejaops.api.handler"]
