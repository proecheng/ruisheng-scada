# Base identity is verified against the authenticated deploy-20260911.2 manifest.
FROM ruisheng-candidate/gw:deploy-20260911.2
COPY ruisheng-gw/src/ /app/ruisheng-gw/src/
COPY ruisheng-shared/src/ /app/ruisheng-shared/src/
