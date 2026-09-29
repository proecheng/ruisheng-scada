# Base identity is verified against the authenticated deploy-20260914.3 manifest.
FROM ruisheng-candidate/web:deploy-20260914.3
COPY ruisheng-web/dist/ /usr/share/nginx/html/
