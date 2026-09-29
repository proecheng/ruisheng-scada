# Base identity is verified against the authenticated deploy-20260914.3 manifest.
FROM ruisheng-candidate/api:deploy-20260914.3
COPY ruisheng-api/src/ /app/ruisheng-api/src/
COPY ruisheng-shared/src/ /app/ruisheng-shared/src/
