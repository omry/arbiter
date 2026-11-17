# Owner clarification: WhatsApp account policy

On 2026-11-19, the owner explicitly selected:

> Per WhatsApp account (Recommended): keep this plugin within Arbiter's current trusted-local boundary; caller authentication stays in its separate project.

The question stated that current Arbiter callers share access to all configured accounts. This decision resolves HLD finding R1-1: "per-agent policy" means policy per WhatsApp provider agent/account, not permissions per authenticated Arbiter caller.

Current runtime caller authentication and account-grant enforcement are absent. The design must state that every reachable caller shares the configured account policies and that account names do not isolate callers. The plugin's operator administration and private provider credentials remain separate from agent operation arguments.

This clarification does not approve caller-authentication implementation, relax provider creator-only enforcement, change the eight policy effects or grant product implementation authority. Affected requirements and HLD review must bind the clarified documents before dependent planning.
