# Base identity is checked against the signed deploy-20260915.2 manifest.
FROM ruisheng-candidate/api:deploy-20260915.2
COPY ruisheng-api/src/ /app/ruisheng-api/src/
