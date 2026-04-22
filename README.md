# LeadPulse MCP

Artifact producer for the LeadPulse campaign-processor container.

This repo builds the Nuitka-compiled Docker image and publishes it to
DockerHub. It does **not** orchestrate any infrastructure.

## Build + publish

```bash
./scripts/build-and-push.sh
# Produces projexlight/projex-leadpulse-mcp:<git-sha> and :latest
```

## Deployment

Deployment, ECS task definition, autoscaling policy, and CloudWatch
alarms are owned by the LeadPulse CRM:

    projex_crm/aws/task-definition.json
    projex_crm/aws/deploy.sh
    projex_crm/aws/cloudwatch-alarms.json

The CRM admin portal triggers deploys; this repo only needs to publish
the image. The image tag scheme the CRM expects is
`projexlight/projex-leadpulse-mcp:<git-sha>` (also pushed as `:latest`).
