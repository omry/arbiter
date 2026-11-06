# Predicted lines before and after redesign

Before is the exact forecast frozen before this redesign: added-plus-deleted edit churn.
After is incremental final delivered nonblank physical content, excluding comment-only lines and counting docstrings physically.
These are **different units**. Lower after values do not establish percentage savings or smaller implementation effort.
The original1,000 authored-changed-line cap remains a separate constraint. CI shown as unknown before is not zero.
All51 cut IDs and nine milestone obligations are preserved; no split/merge mapping is needed.
Completed1–2, including2.10, are excluded from remaining estimates.
Current estimates include the accepted normal rotation wire/preflight and portable issuer/recovery-availability corrections.

## Milestone rollups

| Milestone | Cuts before / after | Before production churn | Before tests churn | Before CI churn | Before combined churn | After production final | After tests final | After CI final | After combined final |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 3 | 13 / 13 | 2,350–3,360 | 4,440–5,980 | 0–70 | 6,790–9,410 | 2,020–2,990 | 3,730–5,210 | 0–15 | 5,750–8,215 |
| 4 | 5 / 5 | 960–1,640 | 1,420–2,440 | unknown | 2,380–4,080 | 930–1,430 | 1,360–1,990 | 0–10 | 2,290–3,430 |
| 5 | 5 / 5 | 960–1,650 | 1,320–2,280 | unknown | 2,280–3,930 | 1,150–1,730 | 1,440–2,120 | 0–0 | 2,590–3,850 |
| 6 | 4 / 4 | 840–1,430 | 1,080–1,880 | unknown | 1,920–3,310 | 730–1,170 | 970–1,430 | 0–0 | 1,700–2,600 |
| 7 | 6 / 6 | 1,160–2,000 | 1,680–2,920 | unknown | 2,840–4,920 | 960–1,540 | 1,470–2,200 | 0–15 | 2,430–3,755 |
| 8 | 5 / 5 | 810–1,430 | 1,300–2,290 | unknown | 2,110–3,720 | 670–1,130 | 1,140–1,740 | 0–10 | 1,810–2,880 |
| 9 | 5 / 5 | 990–1,700 | 1,420–2,450 | unknown | 2,410–4,150 | 860–1,370 | 1,240–1,870 | 0–10 | 2,100–3,250 |
| 10 | 5 / 5 | 920–1,580 | 1,460–2,520 | unknown | 2,380–4,100 | 760–1,250 | 1,260–1,900 | 0–10 | 2,020–3,160 |
| 11 | 3 / 3 | 480–860 | 860–1,500 | unknown | 1,340–2,360 | 380–670 | 780–1,170 | 0–10 | 1,160–1,850 |

After project totals: production **8,460–13,280**, tests **13,390–19,630**, ci **0–80**, combined **21,850–32,990**.
Before combined is24,450–39,980 churn, with unquantified CI outside milestone3.
Production+tests final-content total: 21,850–32,910. CI is separately added to combined.

## Every preserved cut

| Slice | Before production / tests / CI / combined churn | After production / tests / CI / combined final | Predicted authored churn upper |
| --- | --- | --- | ---: |
| `arbiter-authentication/3.1.1` — Shared bounded carrier and streaming peer | 320–440 / 300–420 / 0–5 / 620–865 | 220–310 / 250–360 / 0–5 / 470–675 | 855 |
| `arbiter-authentication/3.1.2` — Strict ordinary server TLS peer | 170–240 / 310–420 / 0–5 / 480–665 | 150–220 / 260–370 / 0–0 / 410–590 | 670 |
| `arbiter-authentication/3.1.3` — Verified Go native HTTP connection | 190–260 / 300–410 / 0–5 / 490–675 | 170–240 / 250–350 / 0–0 / 420–590 | 670 |
| `arbiter-authentication/3.1.4` — One-request HTTP to ASGI stream | 230–320 / 350–470 / 0–5 / 580–795 | 210–290 / 300–420 / 0–0 / 510–710 | 790 |
| `arbiter-authentication/3.2.1` — Live enrollment and receipt denial | 230–330 / 420–610 / 0–5 / 650–945 | 210–310 / 370–510 / 0–0 / 580–820 | 920 |
| `arbiter-authentication/3.2.2` — Grant controls and dispatch coordinator | 220–300 / 330–430 / 0–5 / 550–735 | 200–280 / 280–390 / 0–0 / 480–670 | 750 |
| `arbiter-authentication/3.3` — Grant-filtered discovery and direct lookup | 160–230 / 320–440 / 0–5 / 480–675 | 150–220 / 270–380 / 0–0 / 420–600 | 680 |
| `arbiter-authentication/3.4` — Authorized plugins with closed artifacts | 230–320 / 350–460 / 0–5 / 580–785 | 190–280 / 300–410 / 0–0 / 490–690 | 770 |
| `arbiter-authentication/3.6.1` — Cross-connection denial and worker ownership | 190–270 / 350–470 / 0–5 / 540–745 | 160–240 / 300–420 / 0–0 / 460–660 | 740 |
| `arbiter-authentication/3.6.2` — Final-write revocation barrier | 150–220 / 330–450 / 0–5 / 480–675 | 130–200 / 280–390 / 0–0 / 410–590 | 670 |
| `arbiter-authentication/3.5.1` — Explicit enrollment selection across CLI | 130–180 / 320–410 / 0–5 / 450–595 | 110–170 / 260–360 / 0–0 / 370–530 | 610 |
| `arbiter-authentication/3.5.2` — Atomic authenticated API cutover | 110–190 / 380–510 / 0–5 / 490–705 | 100–170 / 310–430 / 0–0 / 410–600 | 680 |
| `arbiter-authentication/3.7` — Installed native transport integration | 20–60 / 380–480 / 0–10 / 400–550 | 20–60 / 300–420 / 0–10 / 320–490 | 570 |
| `arbiter-authentication/4.1` — Due rotation with a fixed deadline | 220–360 / 260–450 / unknown / 480–810 | 250–370 / 280–410 / 0–0 / 530–780 | 860 |
| `arbiter-authentication/4.2` — Durable candidate activation and reconciliation | 240–400 / 300–500 / unknown / 540–900 | 280–390 / 320–450 / 0–0 / 600–840 | 920 |
| `arbiter-authentication/4.3` — Purpose-bound late completion | 260–420 / 300–520 / unknown / 560–940 | 230–350 / 280–410 / 0–0 / 510–760 | 840 |
| `arbiter-authentication/4.4` — One authenticated predecessor attempt | 160–280 / 240–420 / unknown / 400–700 | 140–230 / 210–320 / 0–0 / 350–550 | 630 |
| `arbiter-authentication/4.5` — Lifecycle process and clock integration | 80–180 / 320–550 / unknown / 400–730 | 30–90 / 270–400 / 0–10 / 300–500 | 580 |
| `arbiter-authentication/5.1` — Pairing repair of an existing enrollment | 180–300 / 240–420 / unknown / 420–720 | 170–270 / 230–350 / 0–0 / 400–620 | 700 |
| `arbiter-authentication/5.2` — Offline owner authorization files | 220–370 / 280–480 / unknown / 500–850 | 240–360 / 300–420 / 0–0 / 540–780 | 860 |
| `arbiter-authentication/5.3` — Atomic access recovery and exact receipt | 200–330 / 260–450 / unknown / 460–780 | 220–330 / 260–390 / 0–0 / 480–720 | 800 |
| `arbiter-authentication/5.4` — Portable replacement across enrolled servers | 180–320 / 280–480 / unknown / 460–800 | 280–410 / 340–490 / 0–0 / 620–900 | 980 |
| `arbiter-authentication/5.5` — Owner issuer and portable certificate renewal | 180–330 / 260–450 / unknown / 440–780 | 240–360 / 310–470 / 0–0 / 550–830 | 910 |
| `arbiter-authentication/6.1` — Planned server-key transition publication | 200–340 / 260–450 / unknown / 460–790 | 190–300 / 230–340 / 0–0 / 420–640 | 720 |
| `arbiter-authentication/6.2` — Verified server transition adoption | 200–340 / 260–450 / unknown / 460–790 | 170–280 / 240–350 / 0–0 / 410–630 | 710 |
| `arbiter-authentication/6.3` — Independent compromise recovery | 220–370 / 280–480 / unknown / 500–850 | 190–300 / 260–380 / 0–0 / 450–680 | 760 |
| `arbiter-authentication/6.4` — Server authority and leaf certificate renewal | 220–380 / 280–500 / unknown / 500–880 | 180–290 / 240–360 / 0–0 / 420–650 | 730 |
| `arbiter-authentication/7.1` — Recipient possession and bounded retention | 220–370 / 260–450 / unknown / 480–820 | 180–280 / 220–330 / 0–0 / 400–610 | 690 |
| `arbiter-authentication/7.2` — Dual-recipient encrypted artifact writing | 240–400 / 280–480 / unknown / 520–880 | 220–330 / 260–390 / 0–5 / 480–725 | 805 |
| `arbiter-authentication/7.3` — Physical quotas and encrypted-store restart | 220–360 / 280–480 / unknown / 500–840 | 200–300 / 240–360 / 0–0 / 440–660 | 740 |
| `arbiter-authentication/7.4` — Creator-bound descriptor HEAD and one GET | 220–370 / 300–500 / unknown / 520–870 | 190–290 / 260–380 / 0–0 / 450–670 | 750 |
| `arbiter-authentication/7.5` — Encrypted plugin adapter activation | 180–320 / 260–460 / unknown / 440–780 | 140–240 / 230–340 / 0–0 / 370–580 | 660 |
| `arbiter-authentication/7.6` — Encrypted-store contention and packaging | 80–180 / 300–550 / unknown / 380–730 | 30–100 / 260–400 / 0–10 / 290–510 | 590 |
| `arbiter-authentication/8.1` — Immediate decrypted stdin with explicit stream flag | 180–320 / 260–450 / unknown / 440–770 | 170–270 / 220–340 / 0–0 / 390–610 | 690 |
| `arbiter-authentication/8.2` — Verified ciphertext staging then stdin | 260–420 / 300–520 / unknown / 560–940 | 220–340 / 260–390 / 0–0 / 480–730 | 810 |
| `arbiter-authentication/8.3` — Explicit verified plaintext temporary path | 150–260 / 220–400 / unknown / 370–660 | 130–220 / 200–310 / 0–0 / 330–530 | 610 |
| `arbiter-authentication/8.4` — Verified persistent plaintext export | 140–250 / 220–400 / unknown / 360–650 | 120–210 / 200–310 / 0–0 / 320–520 | 600 |
| `arbiter-authentication/8.5` — Four-path installed process integration | 80–180 / 300–520 / unknown / 380–700 | 30–90 / 260–390 / 0–10 / 290–490 | 570 |
| `arbiter-authentication/9.1` — Coherent saved configuration and actual secrets | 240–400 / 280–480 / unknown / 520–880 | 240–360 / 260–390 / 0–0 / 500–750 | 850 |
| `arbiter-authentication/9.2` — Fenced SQLite and saved-state snapshot | 200–340 / 280–470 / unknown / 480–810 | 180–280 / 230–350 / 0–0 / 410–630 | 710 |
| `arbiter-authentication/9.3` — Strict signed recovery stream format | 250–420 / 300–520 / unknown / 550–940 | 220–340 / 260–390 / 0–0 / 480–730 | 810 |
| `arbiter-authentication/9.4` — Encrypted atomic local export | 220–360 / 280–480 / unknown / 500–840 | 200–310 / 240–360 / 0–0 / 440–670 | 750 |
| `arbiter-authentication/9.5` — External backup job and snapshot integration | 80–180 / 280–500 / unknown / 360–680 | 20–80 / 250–380 / 0–10 / 270–470 | 550 |
| `arbiter-authentication/10.1` — Strict offline decrypt and validation | 240–400 / 300–520 / unknown / 540–920 | 200–310 / 260–390 / 0–0 / 460–700 | 780 |
| `arbiter-authentication/10.2` — Exclusive restore staging and cleanup | 200–340 / 280–480 / unknown / 480–820 | 180–280 / 230–350 / 0–0 / 410–630 | 710 |
| `arbiter-authentication/10.3` — Atomic active and immutable undo publication | 220–360 / 300–520 / unknown / 520–880 | 190–300 / 260–390 / 0–0 / 450–690 | 770 |
| `arbiter-authentication/10.4` — Repeatable latest undo and explicit discard | 180–300 / 260–450 / unknown / 440–750 | 160–260 / 230–350 / 0–0 / 390–610 | 690 |
| `arbiter-authentication/10.5` — Native restore crash and client integration | 80–180 / 320–550 / unknown / 400–730 | 30–100 / 280–420 / 0–10 / 310–530 | 610 |
| `arbiter-authentication/11.1` — Maintenance drain and cooperative migration | 180–300 / 260–450 / unknown / 440–750 | 170–280 / 240–360 / 0–0 / 410–640 | 720 |
| `arbiter-authentication/11.2` — Authenticated fixed URL handoff | 220–380 / 280–500 / unknown / 500–880 | 180–290 / 240–370 / 0–0 / 420–660 | 740 |
| `arbiter-authentication/11.3` — Multi-client migration and whole-auth integration | 80–180 / 320–550 / unknown / 400–730 | 30–100 / 300–440 / 0–10 / 330–550 | 630 |

Ranges are low-confidence forecasts from the consumed mechanisms and native test obligations, not measured bounds. Static fixture data/generated outputs and development docs remain additional unquantified burden. Recheck actual authored additions plus deletions before each cut; do not compress code or remove tests to fit.
