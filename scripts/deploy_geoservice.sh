#!/usr/bin/env bash
# Roll out a FeatureServer or ImageServer image under the `prod` tag, then clear the api host's CDN
# cache if what the image serves changed. Run by cloudbuild.yaml's deploy-geoservices and
# deploy-imageserver steps.
#
#   scripts/deploy_geoservice.sh SERVICE DOCKERFILE VERSION_ARG CLEAR_PATH [gcloud run deploy flags]
#
# Env: REGION, CDN_URL_MAP, CDN_PROJECT, CDN_HOST.
# SERVED_BUILD is the release the Dockerfile pins plus a hash of the Dockerfile, so an ENV edit
# counts too. It is compared with the revision that takes the most traffic, not the latest one or
# the `prod` tag, so a rerun after a failed traffic move still sees the old build serving.
set -euo pipefail
service=$1 dockerfile=$2 version_arg=$3 clear_path=$4
shift 4

version=$(sed -n "s/^ARG ${version_arg}=//p" "$dockerfile" | head -n1 | tr -d "\"' " | tr . -)
[ -n "$version" ] || { echo "no ${version_arg} in ${dockerfile}" >&2; exit 1; }
build="${version}-$(sha256sum "$dockerfile" | cut -c1-8)"

serving_revision=$(gcloud run services describe "$service" --region="$REGION" --format=json \
  | python3 -c 'import json, sys
live = [t for t in json.load(sys.stdin).get("status", {}).get("traffic", [])
        if t.get("percent", 0) > 0 and t.get("revisionName")]
print(max(live, key=lambda t: t["percent"])["revisionName"] if live else "")' || true)
served=""
if [ -n "$serving_revision" ]; then
  served=$(gcloud run revisions describe "$serving_revision" --region="$REGION" --format=json \
    | python3 -c 'import json, sys
env = json.load(sys.stdin)["spec"]["containers"][0].get("env", [])
print(next((e.get("value", "") for e in env if e["name"] == "SERVED_BUILD"), ""))')
fi

gcloud run deploy "$service" "$@" --update-env-vars="SERVED_BUILD=$build" --tag=prod
gcloud run services update-traffic "$service" --region="$REGION" --to-tags=prod=100

if [ "$served" = "$build" ]; then
  echo "$service already served $build; CDN cache kept"
  exit 0
fi
clear=(gcloud compute url-maps invalidate-cdn-cache "$CDN_URL_MAP" --project="$CDN_PROJECT"
       --host="$CDN_HOST" --path="$clear_path" --async)
echo "$service now serves $build (was ${served:-unknown}); clearing $CDN_HOST$clear_path"
if ! "${clear[@]}"; then
  printf -v cmd '%q ' "${clear[@]}"
  echo "CDN cache NOT cleared, and a rerun of this build will not clear it. Run: $cmd" >&2
  exit 1
fi
