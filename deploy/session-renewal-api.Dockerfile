# Base identity must match the authenticated deploy-20260915.1 manifest.
FROM ruisheng-candidate/api:deploy-20260915.1
COPY ruisheng-api/src/ /app/ruisheng-api/src/
