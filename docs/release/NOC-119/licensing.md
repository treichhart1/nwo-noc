# License acceptance is an independent release gate

Pinned Redis is7.4.12, not Redis8. The exact upstream license already retained in
NOC117 is [Redis7.4.12 LICENSE.txt](https://github.com/redis/redis/blob/7.4.12/LICENSE.txt).
It offers RSALv2 or SSPLv1. Redis8 licensing statements must not be retroactively
applied to this binary. No substitute Redis/fork or major upgrade is introduced.

The operator/legal reviewer must select the applicable terms and review internal
use, redistribution of container artifacts, modification/source notices and any
offering of Redis functionality to third parties. RSALv2 restricts providing the
software's functionality as a third-party service and requires preservation of
notices under its terms. SSPLv1 contains source/distribution and network-service
obligations that require deployment-specific review. The NOC's own customer-facing
status page does not by itself establish compliance or violation of either license.
This is a checklist, not legal advice or approval. No hosted-service exception is
inferred from a container scanner calling an OS package BSD/MIT.

Before approval retain: exact image/Redis binary version and digest, selected
license text, reviewer's identity/date, planned deployment and redistribution
description, dependency notices, source offers/disclosure obligations where
applicable, and a signed independent decision. Keep this outside customer/public
status APIs. A CI run or generated SPDX document cannot supply that decision.

Full native license observations and package inventories are preserved by Trivy
and per-image SPDX in each CI archive; source lock SPDX is separate. Unknown,
custom, proprietary, ambiguous, multi-license, GPL/LGPL and vendored native-wheel
components require component/source review rather than automatic acceptance by
label. Preserve notices and obligations for Ubuntu packages, GDAL/PROJ, Python,
OpenSSL and build tools as applicable; unused build-only dependencies are not
therefore exempt from build/distribution review. Scanner completeness must be
reviewed against actual artifacts. Operational release remains BLOCKED pending
legal/operator acceptance and trusted publication evidence.
