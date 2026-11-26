# PITCH.md

Six lines and a lever. Your words. The last two are scored.

Built: A Larkspur disruption-care chat agent on the Claude Messages API: the nine given tools plus four team tools, two of them discovered live from our MCP server.
Does: Reads the real booking, flight status and policy row before it speaks, offers rebooking options it can actually hold, and leaves the final confirm click to the customer.
Number: 5/6 scored eval cases passed (83%), n=6 cases, 1 run each, judge claude-opus-5-5.
Safety check: Hard gates proved it never books without the customer's own confirmation token, never accepts a flight number that is not on the booking, and rebooks a family of four only onto flights with four seats.
Next: Author TONE_ADDENDUM in Build 4 and measure before/after on the bench.
Still broken: Nothing watches tone - an abusive customer threatening a lawyer still gets a normal entitlements rundown and a voucher instead of an escalation (tone-0101, hard gate, blocks the release).
Lever: <cost | speed | intelligence>

## Priya asked

Costs:
Wrong:
Runs it:
Left out:
