# archon — ideas & open decisions

Running design notes. The README holds the current concept; this file holds the reasoning, the grill defenses, and the open forks that aren't settled yet.

---

## The core existence question: "Opus can already do this out of the box"

This is the make-or-break concern. For a common web stack (Next.js on Vercel, worker on Railway, Postgres+auth on Supabase), Opus is already excellent — so the *Recursive Intelligence* delta curve is flat (the base model nails run 1, nothing to improve), and "just point Claude Code at it" is the real baseline.

**The reframe that answers it:** archon does **not** compete on architecture smarts (Opus wins there). It competes on the two things a frontier model structurally cannot do:

1. **Opus can't measure before it deploys.** Real cost / p95 under real load isn't in any weights.
2. **Opus is stateless across runs.** Its miss-distance from a target is constant; archon's shrinks.

Differentiator = **grounded experience + memory, not intelligence.**

---

## Grill defenses (the reframe survived two hard rounds)

### Round A — "what does archon learn that is non-pretrainable *and* transfers across app types?"

Concession: absolute per-workload perf ("Railway small does X rps for the scraper") is non-pretrainable but **does not transfer** to a differently-shaped app. Public specs/pricing **transfer but Opus already has them.** If those were the only two piles, the intersection is empty and archon is dead.

**The answer — the calibration layer.** archon should learn the **systematic offset between what specs/Opus predict and what this environment actually delivers**, not the absolute numbers:
- "In this account/region, discount Opus's throughput prediction ~40% and add ~400ms cold start."
- "Supabase pooler errors at ~60% of documented max connections."
- "Effective cost per 1M requests bills at ~1.7× the list estimate once egress is counted."

A correction factor is workload-*independent* — applied on top of whatever Opus predicts for the next app — so it **transfers**, and it's **non-pretrainable** (this account, this region, this moment). That intersection is not empty; it's the calibration/correction pile.

**Inner loop ≠ autoscaling:** turning one continuous knob until p95 drops *is* autoscaling. But hitting a joint cost+latency target needs **architectural** moves in a discrete, combinatorial space (edge vs. server, cache, read replica, pooling, service split, platform choice). An autoscaler can't re-architect; that search needs the LLM + the learned prior. The intelligence is the architectural search, not the knob-turn.

### Round B — "you only beat Opus by tying its hands"

Concession: Opus-one-shot is a strawman. The fair baseline is **looped-Opus** (Opus proposing, reading the same telemetry, adjusting). Must benchmark against that.

**The honest three-arm experiment:**
- **looped-Opus** — stateless; re-discovers the environment's calibration from scratch every run, pays the same convergence cost forever. Flat line.
- **archon-cold** — ties looped-Opus early (no magic on a fresh environment — conceded).
- **archon-warm** — starts from accumulated calibration, first proposal lands closer to target, converges in fewer iterations. The gap vs. looped-Opus **is** the value of cross-run memory.

The only claim: **cross-run calibration memory beats re-discovering the environment every run.** If the calibration doesn't transfer, archon-warm ties looped-Opus and the project shouldn't exist. The demo must measure exactly this (looped-Opus vs. archon-warm on a **held-out app type**), not archon-vs-strawman.

**Toy-measurement concession:** empty-app-under-synthetic-load ≠ production. Fixes: (1) scope the claim/target to the measured regime; (2) seed a realistic dataset (10k–1M rows) + a representative load profile — far better than an empty table; (3) calibration *ratios* (measured ÷ predicted) are more regime-robust than absolute numbers, so the calibration layer degrades gracefully where absolute-perf claims wouldn't. Honest bound: if production-only effects dominate, we can't capture them and we say so.

**Bottom line:** archon is a bet on ONE empirical claim — a per-environment spec-vs-reality calibration is (a) materially wrong for Opus and (b) transferable across app types. The demo's job is to settle that bet, not assert it.

---

## The big insight: AWS / Azure / GCP is a *much* better fit than Vercel/Railway/Supabase

The calibration bet is weak on the simple platforms (flat pricing, few quotas, Opus nails run 1). It is **strong on the big three**, because both grill objections flip in archon's favor.

### What Opus knows *well* about AWS/Azure/GCP
- Service catalogs cold (EC2/S3/Lambda/RDS/VPC/IAM/ECS/EKS + Azure/GCP equivalents).
- IaC fluency (Terraform, CloudFormation, CDK, Bicep, Pulumi).
- Reference architectures (3-tier, serverless, event-driven, Well-Architected pillars).
- CLI/SDK syntax (`aws`, `az`, `gcloud`, boto3).
- "Sketch a reasonable architecture + the Terraform" → excellent, first try. Home turf.

### Where Opus is genuinely weak (the "starts bad at run 1" territory)
1. **IAM exactness** — plausible-looking policies that are subtly wrong (missing action, wrong condition key, ARN format, broken trust relationship, resource- vs. identity-policy confusion). Where deploys die with `AccessDenied`. Its single most error-prone AWS surface. Crisp, measurable failure signal.
2. **Account-specific reality it *cannot* know** — actual service quotas (per-account, per-region, often lower than docs and frequently hit: EIPs, vCPU, Lambda concurrency), SCPs / org policies that deny, which services exist in which region, existing VPC/networking, naming/tagging policies, existing security groups.
3. **Cost** — Byzantine pricing (NAT gateway data-processing, cross-AZ transfer, S3 request tiers, egress). Opus mis-estimates the real bill by a *large* margin — far more than Railway's flat pricing.
4. **Wiring surface area** — VPC + subnets + route tables + NAT + security groups + endpoints + IAM: an order of magnitude more to get wrong. More failure modes = more for a learning loop.
5. **API/feature drift** past training cutoff.

### Why this strengthens archon on *both* grill axes
- **"Non-pretrainable that transfers" pile is fat here.** Account-specific reality — your quota ceilings, your SCPs, which IAM patterns actually work in this account, region availability, real effective cost — is unknowable to Opus **and** applies to *every* app you deploy into that account regardless of workload. Exactly the Round-A intersection, and rich instead of thin.
- **"Opus starts bad at run 1" becomes true.** On AWS, Opus's first `terraform apply` genuinely fails (IAM denial, quota hit, region-unavailable service). Real room for the recursive loop to show dumb→sharp — the thing the track rewards.

The big clouds flip both objections in archon's favor. The calibration bet is a *much* better bet against AWS than against Railway.

---

## The tradeoff: demo safety vs. existence case

The original reason for Vercel/Railway/Supabase was **demo safety** — fast, cheap, low blast radius, deployable live on stage. The big clouds are the opposite: slow deploys, real spend, real blast radius, `terraform apply` that can hang/fail in front of judges, IAM/quota failures that are the *point* but also a demo risk.

| | Simple platforms (Vercel/Railway/Supabase) | Big clouds (AWS/Azure/GCP) |
|---|---|---|
| Existence case | Weak — Opus nails run 1, flat curve | **Strong** — Opus struggles, rich transferable calibration |
| Non-pretrainable-that-transfers | Thin | **Fat** (quotas, SCPs, IAM, region, real cost) |
| Demo safety | **High** — fast, cheap, safe | Low — slow, costly, high blast radius |
| Live-on-stage risk | Low | High |

### The middle path (get both)
Target a big cloud but **bound the blast radius**:
- **LocalStack** (local AWS emulation) for the live demo — fast, free, and it still exercises real IAM / quota / wiring **failure modes** (the "Opus starts bad" signal) without live-deploy risk.
- A **sandbox account with tight budget alarms** for one real deploy as the "it's not a toy" proof.
- Keep the human gate + HiddenLayer + dry-run before every real action.

This plausibly gets the strong existence case *and* a demo that can't blow up on stage.

---

## Open decisions (not yet settled)

1. **Platform target** — commit to a big cloud (which one?) vs. stay on the simple platforms. Leaning big-cloud for the existence case, pending the demo-risk answer.
2. **Demo substrate** — LocalStack vs. a real sandbox account vs. hybrid (LocalStack live + one real deploy).
3. **Which cloud** — AWS (richest IAM/quota pain = best existence case, most Opus errors to learn from), Azure, or GCP. AWS is the strongest calibration target; also the one Opus is most-trained-on (cuts both ways).
4. **Whether the calibration bet actually holds** — the demo must be designed to *measure* looped-Opus vs. archon-warm on a held-out app type, and be honest if the bet loses.
5. **Load/measurement fidelity** — seeded dataset + representative load profile; how far to push realism vs. hackathon time.
6. **NVIDIA hook** — still weak; local model for the architecting step, not load-bearing. Unresolved.

---

## TODO next
- [ ] Decide platform target + demo substrate (LocalStack?).
- [ ] Work through what archon looks like retargeted at AWS specifically — the failure modes LocalStack reproduces, the calibration facts it would learn, the three-arm experiment design.
- [ ] Update the README once the platform decision is made (currently written for Vercel/Railway/Supabase).
