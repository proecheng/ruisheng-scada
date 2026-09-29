# Base identity must match the authenticated deploy-20260915.1 manifest.
FROM ruisheng-candidate/web:deploy-20260915.1
COPY ruisheng-web/dist/ /usr/share/nginx/html/
