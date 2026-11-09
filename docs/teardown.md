# Project shutdown — November 9, 2026

The user requested removal of the project's infrastructure and consolidation
onto `main`. They explicitly chose to retain the domain registration and
OpenRouter API key. Do not recreate infrastructure or resume paid AI jobs
without a new explicit request. Source code, local corpora, and paid response
caches are preserved.

AWS CloudFormation stack `please-mend-my` in `us-east-1` reached
`DELETE_COMPLETE` at **2026-11-09 16:58:26 UTC**. All 11 stack resources report
`DELETE_COMPLETE`. Native EC2 API checks verified:

| Resource | Final state |
| --- | --- |
| Instance `i-00b3468454b650217` | Terminated |
| 64 GiB root volume `vol-0d53503603a121231` | Deleted; `InvalidVolume.NotFound` |
| Elastic IP `32.193.25.68`, allocation `eipalloc-06c4976e0d6b4a991` | Released; `InvalidAllocationID.NotFound` |
| VPC `vpc-0e9fd084e829332c4` and stack networking | Deleted |
| Imported EC2 key pair `key-0ebda4c0637a2b595` | Deleted separately after stack deletion |

Before deletion, the web and ingestion services were stopped, the web service
was disabled, and the host was powered off. Cloudflare's apex A and www CNAME
were deleted; the zone has no remaining DNS records. Its plan is `Free Website`
with price zero. The free zone and existing registrar settings remain intact.

A `Project=please-mend-my` tag inventory covered all 17 enabled AWS regions;
matches were confined to the deleted deployment in `us-east-1`. Additional
checks found no project snapshots, project AMIs, matching S3 bucket names,
Route 53 hosted zones, or CloudFront distributions. Native resource lookups
confirmed deletion even while the tagging API still returned stale entries.
There were no active EC2 Reserved Instances or active/queued Savings Plans.
No local project systemd units, scheduled unit files, GitHub Actions workflows,
or GitHub deployments were found. Qwen remains paused, and the OpenRouter key
reported zero usage for the day during the shutdown check. Its monthly spending
limit remains $10.

The four commits after the previous `main` tip were fast-forwarded from `demo`
to `main` at `1e9c311`. The remote and local `demo` branches were then deleted,
and the shared workspace was switched to `main`. `git ls-remote --heads origin`
confirmed that `main` was the only remote branch. The previous deployment
worktree is detached; its ignored local data remains preserved.

## Remaining costs

Rates below were checked on November 9, 2026, in USD. These are ongoing costs
after shutdown; AWS usage accrued before deletion can still appear on a bill.

| Service | Ongoing cost |
| --- | --- |
| Project AWS hosting, storage, and public IPv4 | $0 after deletion |
| Cloudflare DNS/zone | $0 on the existing free plan |
| Public GitHub source repository | $0 project hosting; no Actions workflows configured |
| `pleasemendmy.com` registration, retained by request | Current published .com renewal price: $11.17/year, about $0.93/month equivalent, before applicable tax or future price changes |
| OpenRouter key, retained by request | $0 while unused; future requests are metered against credits, with the existing $10/month API spending cap. The cap is not a subscription charge. Credit purchases carry the applicable platform fee. |

The domain's registry expiration is **November 7, 2027**. Cloudflare normally
starts auto-renew attempts about 30 days before expiration. The saved token
cannot read Registrar settings, so the actual auto-renew toggle and the eventual
renewal invoice were not verified or changed. OpenRouter account-level
auto-top-up settings were also outside the saved key's management permissions
and were left unchanged.

Published pricing and billing references:

- [Cloudflare's current .com prices](https://pricing.registrar.cloudflare.com/?q=com)
- [Cloudflare renewal timing](https://developers.cloudflare.com/registrar/account-options/renew-domains/)
- [OpenRouter usage billing and credit-purchase fees](https://openrouter.ai/pricing)
- [AWS instance termination and billing](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/terminating-instances.html)
