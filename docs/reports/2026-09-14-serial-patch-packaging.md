# Serial repair packaging for the slow remote link

The complete 20260914.2 release passed signatures and isolated startup but the target link transferred only about 20 MB in 15 minutes. Its upload was paused before invoking the remote Apply phase; candidate staging files remain intact.

The API and gateway patch Dockerfiles preserve the authenticated deploy-20260911.2 runtime and dependencies and copy the complete current application/shared source trees. The release operator must first verify the old candidate signature and exact base image IDs, ensure pyproject/lock/migrations/entrypoint configuration did not change, and confirm no source files were deleted. The existing editable installs continue to resolve the same source paths. Both new images receive the current source revision label. The web image remains the already validated local-time repair image.

The image archive packager retains the old archive's raw tar prefix containing content-addressed blobs, appends new blobs and replaces only top-level image indexes/manifests. Unreferenced prior blobs remain inert data. Each image has exactly one active image descriptor/tag; no duplicate tar members or links are allowed. Full archive identity validation and fresh isolated Docker loading/startup are required before deployment.

Keeping the old compressed prefix makes a byte-copy patch small without modifying either historical candidates or application code inside running containers. The target reconstructs a complete new signed candidate in a new protected staging directory and checks old, patch and final SHA-256 values. The standard updater still validates the entire signed package before installing it. The database remains at 0013_serial_polling_profile.
