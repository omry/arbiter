# Authentication: remaining work

The goal is authenticated, owner-controlled Arbiter access with encrypted artifacts
and usable recovery. Milestones1–2 are merged, including portable enrollment at
a second server. The remaining design covers all milestones3–11.

The [approved requirements](requirements.md) and [reviewed HLD](high-level-design.md)
keep identity separate from URL, local owner administration separate from client
access, live account grants, fixed credential deadlines, four explicit artifact
paths, offline owner recovery, complete backup/restore/undo and cooperative migration.

The [detailed plan](implementation-plan.md) orders native transport/grants/denial,
lifecycle, owner recovery, server continuity, encrypted artifacts, client processing,
backup, restore/undo and migration. It reuses the current authority DB, carrier,
HTTP client, custody, generation and test-driver primitives. Shared foundations
and tests are charged once. [Before/after estimates](supporting/implementation-plan/estimate-comparison.md)
retain all51 cut IDs; old edit churn and new final-content lines are different units.

**Next step:** affected review and owner approval of the complete detailed plan,
then separate implementation authorization. No product implementation is authorized.
No new material owner choice is pending in the draft.

The generated table is a **definition-only view**, rendered without a delivery
campaign. Its Pending label means no execution evidence is attached to this
revised inventory, not that historical merged work should run again. The four
entries explicitly labelled Already merged retain their original planning state
and have zero remaining estimates. Replaced parents are rollups and are excluded
from project totals. A later delivery must use verified original completion receipts.

Line ranges below are incremental final delivered production/test content, not
added-plus-deleted churn. The separate handwritten-CI forecast is in the comparison.

<!-- awd:slice-table:start -->
| Status | Slice ID | Short description | Production lines | Test lines |
| --- | --- | --- | ---: | ---: |
| ↪ Replaced | `arbiter-authentication/2` | Already merged: Pairing and durable enrollment | 0–0 | 0–0 |
| ↪ Replaced | `arbiter-authentication/2.10` | Already merged: Existing portable identity enrollment | 0–0 | 0–0 |
| ⏳ Pending | `arbiter-authentication/2.10.1` | Already merged: Local issuer trust and unique agent bindings | 0–0 | 0–0 |
| ⏳ Pending | `arbiter-authentication/2.10.2` | Already merged: Portable source custody and versioned codecs | 0–0 | 0–0 |
| ⏳ Pending | `arbiter-authentication/2.10.3` | Already merged: Prepared reuse and single activation | 0–0 | 0–0 |
| ⏳ Pending | `arbiter-authentication/2.10.4` | Already merged: Public CLI reuse and installed acceptance | 0–0 | 0–0 |
| ↪ Replaced | `arbiter-authentication/3` | Aggregate 3; execute its children | 2,020–2,990 | 3,730–5,210 |
| ↪ Replaced | `arbiter-authentication/3.1` | Aggregate 3.1; execute its children | 750–1,060 | 1,060–1,500 |
| ↪ Replaced | `arbiter-authentication/3.2` | Aggregate 3.2; execute its children | 410–590 | 650–900 |
| ↪ Replaced | `arbiter-authentication/3.5` | Aggregate 3.5; execute its children | 210–340 | 570–790 |
| ↪ Replaced | `arbiter-authentication/3.6` | Aggregate 3.6; execute its children | 290–440 | 580–810 |
| ↪ Replaced | `arbiter-authentication/4` | Aggregate 4; execute its children | 930–1,430 | 1,360–1,990 |
| ↪ Replaced | `arbiter-authentication/5` | Aggregate 5; execute its children | 1,150–1,730 | 1,440–2,120 |
| ↪ Replaced | `arbiter-authentication/6` | Aggregate 6; execute its children | 730–1,170 | 970–1,430 |
| ↪ Replaced | `arbiter-authentication/7` | Aggregate 7; execute its children | 960–1,540 | 1,470–2,200 |
| ↪ Replaced | `arbiter-authentication/8` | Aggregate 8; execute its children | 670–1,130 | 1,140–1,740 |
| ↪ Replaced | `arbiter-authentication/9` | Aggregate 9; execute its children | 860–1,370 | 1,240–1,870 |
| ↪ Replaced | `arbiter-authentication/10` | Aggregate 10; execute its children | 760–1,250 | 1,260–1,900 |
| ↪ Replaced | `arbiter-authentication/11` | Aggregate 11; execute its children | 380–670 | 780–1,170 |
| ⏳ Pending | `arbiter-authentication/3.1.1` | Shared bounded carrier and streaming peer | 220–310 | 250–360 |
| ⏳ Pending | `arbiter-authentication/3.1.2` | Strict ordinary server TLS peer | 150–220 | 260–370 |
| ⏳ Pending | `arbiter-authentication/3.1.3` | Verified Go native HTTP connection | 170–240 | 250–350 |
| ⏳ Pending | `arbiter-authentication/3.1.4` | One-request HTTP to ASGI stream | 210–290 | 300–420 |
| ⏳ Pending | `arbiter-authentication/3.2.1` | Live enrollment and receipt denial | 210–310 | 370–510 |
| ⏳ Pending | `arbiter-authentication/3.2.2` | Grant controls and dispatch coordinator | 200–280 | 280–390 |
| ⏳ Pending | `arbiter-authentication/3.3` | Grant-filtered discovery and direct lookup | 150–220 | 270–380 |
| ⏳ Pending | `arbiter-authentication/3.4` | Authorized plugins with closed artifacts | 190–280 | 300–410 |
| ⏳ Pending | `arbiter-authentication/3.6.1` | Cross-connection denial and worker ownership | 160–240 | 300–420 |
| ⏳ Pending | `arbiter-authentication/3.6.2` | Final-write revocation barrier | 130–200 | 280–390 |
| ⏳ Pending | `arbiter-authentication/3.5.1` | Explicit enrollment selection across CLI | 110–170 | 260–360 |
| ⏳ Pending | `arbiter-authentication/3.5.2` | Atomic authenticated API cutover | 100–170 | 310–430 |
| ⏳ Pending | `arbiter-authentication/3.7` | Installed native transport integration | 20–60 | 300–420 |
| ⏳ Pending | `arbiter-authentication/4.1` | Due rotation with a fixed deadline | 250–370 | 280–410 |
| ⏳ Pending | `arbiter-authentication/4.2` | Durable candidate activation and reconciliation | 280–390 | 320–450 |
| ⏳ Pending | `arbiter-authentication/4.3` | Purpose-bound late completion | 230–350 | 280–410 |
| ⏳ Pending | `arbiter-authentication/4.4` | One authenticated predecessor attempt | 140–230 | 210–320 |
| ⏳ Pending | `arbiter-authentication/4.5` | Lifecycle process and clock integration | 30–90 | 270–400 |
| ⏳ Pending | `arbiter-authentication/5.1` | Pairing repair of an existing enrollment | 170–270 | 230–350 |
| ⏳ Pending | `arbiter-authentication/5.2` | Offline owner authorization files | 240–360 | 300–420 |
| ⏳ Pending | `arbiter-authentication/5.3` | Atomic access recovery and exact receipt | 220–330 | 260–390 |
| ⏳ Pending | `arbiter-authentication/5.4` | Portable replacement across enrolled servers | 280–410 | 340–490 |
| ⏳ Pending | `arbiter-authentication/5.5` | Owner issuer and portable certificate renewal | 240–360 | 310–470 |
| ⏳ Pending | `arbiter-authentication/6.1` | Planned server-key transition publication | 190–300 | 230–340 |
| ⏳ Pending | `arbiter-authentication/6.2` | Verified server transition adoption | 170–280 | 240–350 |
| ⏳ Pending | `arbiter-authentication/6.3` | Independent compromise recovery | 190–300 | 260–380 |
| ⏳ Pending | `arbiter-authentication/6.4` | Server authority and leaf certificate renewal | 180–290 | 240–360 |
| ⏳ Pending | `arbiter-authentication/7.1` | Recipient possession and bounded retention | 180–280 | 220–330 |
| ⏳ Pending | `arbiter-authentication/7.2` | Dual-recipient encrypted artifact writing | 220–330 | 260–390 |
| ⏳ Pending | `arbiter-authentication/7.3` | Physical quotas and encrypted-store restart | 200–300 | 240–360 |
| ⏳ Pending | `arbiter-authentication/7.4` | Creator-bound descriptor HEAD and one GET | 190–290 | 260–380 |
| ⏳ Pending | `arbiter-authentication/7.5` | Encrypted plugin adapter activation | 140–240 | 230–340 |
| ⏳ Pending | `arbiter-authentication/7.6` | Encrypted-store contention and packaging | 30–100 | 260–400 |
| ⏳ Pending | `arbiter-authentication/8.1` | Immediate decrypted stdin with explicit stream flag | 170–270 | 220–340 |
| ⏳ Pending | `arbiter-authentication/8.2` | Verified ciphertext staging then stdin | 220–340 | 260–390 |
| ⏳ Pending | `arbiter-authentication/8.3` | Explicit verified plaintext temporary path | 130–220 | 200–310 |
| ⏳ Pending | `arbiter-authentication/8.4` | Verified persistent plaintext export | 120–210 | 200–310 |
| ⏳ Pending | `arbiter-authentication/8.5` | Four-path installed process integration | 30–90 | 260–390 |
| ⏳ Pending | `arbiter-authentication/9.1` | Coherent saved configuration and actual secrets | 240–360 | 260–390 |
| ⏳ Pending | `arbiter-authentication/9.2` | Fenced SQLite and saved-state snapshot | 180–280 | 230–350 |
| ⏳ Pending | `arbiter-authentication/9.3` | Strict signed recovery stream format | 220–340 | 260–390 |
| ⏳ Pending | `arbiter-authentication/9.4` | Encrypted atomic local export | 200–310 | 240–360 |
| ⏳ Pending | `arbiter-authentication/9.5` | External backup job and snapshot integration | 20–80 | 250–380 |
| ⏳ Pending | `arbiter-authentication/10.1` | Strict offline decrypt and validation | 200–310 | 260–390 |
| ⏳ Pending | `arbiter-authentication/10.2` | Exclusive restore staging and cleanup | 180–280 | 230–350 |
| ⏳ Pending | `arbiter-authentication/10.3` | Atomic active and immutable undo publication | 190–300 | 260–390 |
| ⏳ Pending | `arbiter-authentication/10.4` | Repeatable latest undo and explicit discard | 160–260 | 230–350 |
| ⏳ Pending | `arbiter-authentication/10.5` | Native restore crash and client integration | 30–100 | 280–420 |
| ⏳ Pending | `arbiter-authentication/11.1` | Maintenance drain and cooperative migration | 170–280 | 240–360 |
| ⏳ Pending | `arbiter-authentication/11.2` | Authenticated fixed URL handoff | 180–290 | 240–370 |
| ⏳ Pending | `arbiter-authentication/11.3` | Multi-client migration and whole-auth integration | 30–100 | 300–440 |
| | **Project total** | Current slices; replaced parents excluded. | **8,460–13,280** | **13,390–19,630** |
<!-- awd:slice-table:end -->
