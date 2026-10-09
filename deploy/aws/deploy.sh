#!/usr/bin/env bash
# PHASE 11 - Build both images and push them to Amazon ECR.
# Prereqs: AWS CLI v2 logged in (aws configure / SSO), Docker running.
# Usage:   AWS_REGION=ap-south-1 ./deploy/aws/deploy.sh
set -euo pipefail

AWS_REGION="${AWS_REGION:-ap-south-1}"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
REGISTRY="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
TAG="${TAG:-$(git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M)}"

aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$REGISTRY"

for svc in backend frontend; do
  repo="ai-sql-assistant-${svc}"
  aws ecr describe-repositories --repository-names "$repo" --region "$AWS_REGION" >/dev/null 2>&1 \
    || aws ecr create-repository --repository-name "$repo" --region "$AWS_REGION" \
         --image-scanning-configuration scanOnPush=true >/dev/null
  docker build -f "Dockerfile.${svc}" -t "${REGISTRY}/${repo}:${TAG}" .
  docker push "${REGISTRY}/${repo}:${TAG}"
  echo "pushed ${REGISTRY}/${repo}:${TAG}"
done

echo
echo "Next: register the task definitions (deploy/aws/ecs-task-definition.json) and update your ECS services."
