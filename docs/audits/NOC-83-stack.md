# NOC-83 dependency audit

Snapshot of fetched `treichhart1/nwo-noc` branch tips. Ancestry is source inclusion,
not a claim that every earlier PR has been individually approved.

PR #85: `feature/rf-80-self-healing-preview` (653bac6) →
`feature/rf-81-validated-self-healing-telemetry` (c7e7edd).
Latest PR CI #422 (43351273663) passed. PR #86: RF-81 →
`feature/noc-82-release-readiness-audit` (34e54e2), whose parent was b27e53a.
Its CI #420 (43350948478) failed on the mixed-case expected reason assertion;
the acceptance failure was solely its backend dependency. #86 is currently
reported unmergeable. This branch replays only #86's audit commit on c7e7edd,
then adds NOC-83 fixes; existing PR branches are untouched.

`main` is 5a5f211; `develop` and the v3.2.0 tag resolve to 13a737f.
Both are ancestors of this candidate. The candidate had 222 commits beyond
main before NOC-83 changes. This is the complete accumulated feature stack,
not a small patch suitable for direct promotion. Review and integrate upstream
PRs in ancestor order, then rerun all checks at the final consolidated SHA.
No merges or tags were created. Proposed PR base remains #86's branch to keep
the dependency explicit; its diff also includes #85's missing assertion fix.

| Branch | Fetched SHA | Ancestor of candidate |
| --- | --- | --- |
| `audit/noc-foundation-2026-11` | `6e3d0acc8799101e78100f00655d962f53e232a0` | yes |
| `develop` | `13a737f32bcf5dad8321f5e725a169805a96caa8` | yes |
| `feature/github-actions` | `13a737f32bcf5dad8321f5e725a169805a96caa8` | yes |
| `feature/noc-73a-rf-telemetry-normalization` | `c7402a6b134b3313fa1397168918c6cc8da1b200` | yes |
| `feature/noc-73b-uisp-rf-adapter` | `a361cc7f469b478585211edc1374cfa6a9a6ca6a` | yes |
| `feature/noc-73c-rf-telemetry-persistence` | `c02b4c1056a2991587aee737f456072e7f0c70c3` | yes |
| `feature/noc-73d-rf-collection-integration` | `19985f98d166c3ce6aa9575e1222dae30b4e6bff` | yes |
| `feature/noc-73e-rf-degradation-detection` | `ecc7def7d5fe3228b505dbf8b7946d45212d99b2` | yes |
| `feature/noc-73f-rf-trend-analysis` | `7af5fb19dd1a55a673a2960f9dd7e246f089db46` | yes |
| `feature/noc-73g-rf-diagnostics-service` | `dc0f90e3a469954f375bbf4f2e157e4db2198b89` | yes |
| `feature/noc-73h-authenticated-rf-api` | `88a213e13df081532d65d8dfecdd69a498145bab` | yes |
| `feature/noc-73i-rf-dashboard` | `e6b1b0aa70947806c5742471cb0d194684371781` | yes |
| `feature/noc-73j-opt-in-rf-collection` | `f2ba3544fce60535e8adcc1ba087c95a6923fa11` | yes |
| `feature/noc-73k-rf-freshness` | `1f67fe1e8d9dc724dcfaa9d881cce0a0d3d8d3af` | yes |
| `feature/noc-73l-rf-collection-health` | `35516689a0f8a0d6ae9de3e5b736cb36a5839249` | yes |
| `feature/noc-73m-rf-collection-history` | `145f0083ffc970b0073f2f94a3ae561a0527a53e` | yes |
| `feature/noc-73n-rf-reliability-retention` | `0301d3928e29beff5f2de070ab647e3cdaa80610` | yes |
| `feature/noc-73o-rf-operational-readiness` | `d6be2c8f823c4008c4de78ad2564b15265254f78` | yes |
| `feature/noc-73p-rf-readiness-audit` | `e7f17882cd5ab2be5e113f1ec9bde90ab86ae1b0` | yes |
| `feature/noc-73q-postgres-rf-integration` | `aff4aeaa8fdb0a2ba2a8234704e9add2fc66fe9b` | yes |
| `feature/noc-73r-rf-end-to-end-validation` | `2889ae7b5268da05ca5b4bd5bb4932a3df3f5152` | yes |
| `feature/noc-73s-rf-query-scalability` | `765cc8c3941108a4b17eebfc4f49d8f10fd116d5` | yes |
| `feature/noc-73t-rf-health-query-optimization` | `f393a9ab6b4e1973b6a6d63ba2e5126b178d875b` | yes |
| `feature/noc-73u-rf-performance-benchmarks` | `3161fe260c539b3d3d31eeb35a7fceaf2d7ffbce` | yes |
| `feature/noc-73v-rf-performance-baselines` | `e56031b5d69855c80fade3fe804954988afe81d0` | yes |
| `feature/noc-73w-rf-rollout-safety` | `3f16619101112a178faf3a3a2f46cd170fecce8b` | yes |
| `feature/noc-73x-rf-staging-validation` | `e7689f027050fe1559b6e7235adc77fc829b0dbe` | yes |
| `feature/noc-73y-rf-failure-recovery` | `527c8b39c9f529521de10f198e416a06ade15119` | yes |
| `feature/noc-73z-rf-interrupted-sync` | `53d89b46b48ae549c85ab1d8f391d1ba078277a2` | yes |
| `feature/noc-74a-rf-data-security-audit` | `b9667d680870f61a3f01504e8afe7d0d254b2c50` | yes |
| `feature/noc-74b-rf-staging-preflight` | `c57123e35197174caefd08b33c945cf0aa051e48` | yes |
| `feature/noc-74c-rf-staging-validation` | `230cfa080b5b8905eac117e41482c93765208716` | yes |
| `feature/noc-74d-rf-release-readiness` | `6c18e657fc2af347533cb391f605e483d459276f` | yes |
| `feature/noc-74e-rf-integration-audit` | `0dbe5dac4a61ad7cfc74cee6e5cb201246c4edec` | yes |
| `feature/noc-74f-rf-staging-rollout` | `4895573fbff9ee559898350f38222055245b58ca` | yes |
| `feature/noc-74g-rf-staging-acceptance` | `06c7a28083df0e6f2a69b99deb8cb12348f3ca9b` | yes |
| `feature/noc-74h-rf-staging-approval` | `57f05d0d9705a38c5de7e5aa365dcdc4df7aaace` | yes |
| `feature/noc-75a-rf-dashboard-integration` | `87d9a5b1bd295f6f9d6031068769638d4bf2391a` | yes |
| `feature/noc-75b-rf-device-correlation` | `813298ce764c0d0b352039541af5a9f0f797c68a` | yes |
| `feature/noc-75c-rf-site-summary` | `065e244899d12d7a9f4470d819db872f76f161af` | yes |
| `feature/noc-75d-rf-site-validation` | `a8dd8b304db596dc2af94e4c61bd309220af1287` | yes |
| `feature/noc-75e-rf-site-query-optimization` | `9d8532a491184fb35510727b68e28640c46c1e03` | yes |
| `feature/noc-75f-rf-query-budget` | `26c3db54dda02704c55e3d3fd97572fb5349e601` | yes |
| `feature/noc-75g-rf-bulk-history` | `22286c8b2dfcb27d0a41fa0cc30ef9271e55b921` | yes |
| `feature/noc-75h-rf-final-review` | `f0b8d24744fc99f1cf2004efc70ccff40b2e8550` | yes |
| `feature/noc-75i-rf-release-preparation` | `1908dbd466f5f3093259d560378757630be0df68` | yes |
| `feature/noc-75j-rf-stack-audit` | `0ddfb750de433c6c29dd8cccee710ae02e728a6f` | yes |
| `feature/noc-75k-foundation-integration-audit` | `8ed179f66e46bc1f1ac4379b6c9c69c5847dc5f8` | yes |
| `feature/noc-75l-consolidated-security-review` | `1317ebd527d77fba40fa9a78387c6fcbf8279a11` | yes |
| `feature/noc-75m-rf-authorization` | `1dfb02fa3f28a9ef2468fa5cc08f63071f0b439b` | yes |
| `feature/noc-75n-rf-security-regression` | `d2d2075da27c77da1136605489a802b1a3321cf5` | yes |
| `feature/noc-75o-deployment-security` | `b4680a6ee7a117003f007c96a117fd331a7c7849` | yes |
| `feature/noc-75p-config-safeguards` | `fff876b754248ed1a641366f50fabbc889d218c4` | yes |
| `feature/noc-75q-compose-network-guard` | `c591bee9287b4050c28a63397ad02cb66448a590` | yes |
| `feature/noc-75r-deployment-preflight` | `7b9810abe123fef6b7c685f4a307063f226e65cb` | yes |
| `feature/noc-75s-readiness-audit` | `8a34ca8c3c64aaeaa88660146723c22db0ed4d0b` | yes |
| `feature/noc-75t-staging-readiness-gate` | `95aa37bddf4a37a07146234705772bfc42f4a5a7` | yes |
| `feature/noc-75u-pr-chain-audit` | `7b06aadde555ece5cf32458c196e4d485ff68363` | yes |
| `feature/noc-75v-staging-procedure` | `33b70c0f7f3a517c4fa62c3f6c28a78b85be4f09` | yes |
| `feature/noc-75w-staging-isolation-check` | `1b20a69685fecd317c879be4b720a32d773891d8` | yes |
| `feature/noc-75x-staging-egress-policy` | `0adcbcdfe56b0eb38ec2c10a02a2d8144ef12857` | yes |
| `feature/noc-75y-staging-network-test-plan` | `5a56b81524bb0acd6d5f6ac4cff4a127d875d9d1` | yes |
| `feature/noc-75z-offline-staging-ci` | `296a9be82202ad59a8a8043feb416465a869ce47` | yes |
| `feature/noc-76a-rf-regression-gate` | `1f9677739845781753f3ede54325b02cbfb5604f` | yes |
| `feature/noc-76b-rf-release-regression` | `871772f92c13ee339c00afaba57d2e56ce6fc7eb` | yes |
| `feature/noc-76c-rf-acceptance-gate` | `667dc2aea76d762b4f22575ccd930e1b4363d705` | yes |
| `feature/noc-76d-rf-completion-review` | `10b697b830c9206b55bbd6713acd5985e5b0927e` | yes |
| `feature/noc-77a-dashboard-contract` | `65c9c394c4c32a4d6c06706dd67c914eb91de0f4` | yes |
| `feature/noc-77b-dashboard-status-consistency` | `f711b0a96a59138e495541fa26114941d75a4fd6` | yes |
| `feature/noc-77c-dashboard-query-performance` | `530619f65961366312cd84f52b97aeb8c035741e` | yes |
| `feature/noc-77d-dashboard-query-budget` | `0ec9493f115be51a2ba631def42bc7fc1596ed8d` | yes |
| `feature/noc-77e-dashboard-large-inventory` | `998919f92fbbc6a836eb7414e45c4c589331439d` | yes |
| `feature/noc-77f-operations-monitoring-contract` | `cc189552f8ea90642b20a530a7c73754eb5aeb30` | yes |
| `feature/noc-77g-monitoring-query-optimization` | `9c51d9916617a913cd330dcbe4e45c12484b01bb` | yes |
| `feature/noc-77h-operations-query-optimization` | `92f8c847b92e96a9c322caa71c2668edba05489a` | yes |
| `feature/noc-77i-operations-postgres` | `dc32980f3993e039b70c8213b0b162c1fd4522cd` | yes |
| `feature/noc-77j-monitoring-postgres` | `a0e7a56a4af23993043ff73147ec46be63cb219a` | yes |
| `feature/noc-77k-monitoring-freshness` | `7a8b17c16eaad02c7f0dc41587cb04d60ee63c6d` | yes |
| `feature/noc-77l-monitoring-freshness-ui` | `cca5b09d985633b7e54859da9b1dd6b6392371ec` | yes |
| `feature/noc-77m-monitoring-status-clarity` | `7c3418c32ca2f38915abc640bfb2836aaa2b4261` | yes |
| `feature/noc-77n-fresh-monitoring-counts` | `6492beb6ae4961bec78f1df87f8e71c6adf90b63` | yes |
| `feature/noc-77o-never-checked-monitoring` | `92a1de46b3f88dc8c11f4b7310b8a854dd4b5122` | yes |
| `feature/noc-77p-monitoring-attention` | `5eec8f2804dc06d3c6f00daeb238c40524c457fe` | yes |
| `feature/noc-78-rf-health-refresh` | `085111e56f80086438527d581b545c08586af44d` | yes |
| `feature/noc-82-release-readiness-audit` | `34e54e226f93159adf7d2dc52548d36aad277b46` | no |
| `feature/rf-79-simulated-self-healing` | `f7856391361cbcbfc0287786b2409a2c69c3d069` | yes |
| `feature/rf-80-self-healing-preview` | `653bac6acd43ea71d301b3e1b2b3a6e779174e8b` | yes |
| `feature/rf-81-validated-self-healing-telemetry` | `c7e7eddca8a044d1efc24d953303cb376daf0f74` | yes |
| `main` | `5a5f21138339f522d7e6de6ad38b83f47d14f83c` | yes |
