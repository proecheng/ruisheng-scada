# Base identity is checked against the signed deploy-20260915.2 manifest.
FROM ruisheng-candidate/web:deploy-20260915.2
COPY ruisheng-web/dist/ /usr/share/nginx/html/
