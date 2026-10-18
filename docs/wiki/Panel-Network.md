# Network

Sentinel separates two questions that are easy to mix up:

- **Does the host answer on the network?** That is *reachability*.
- **Does the device work in Home Assistant?** That is the availability of its entities.

A NAS can answer ping while its integration is broken, and a lease can expire while the
device is fine. Sentinel shows both states side by side and never derives one from the other.

## Level N0 (default): no scanning

Sentinel only uses what Home Assistant already knows:

- **Devices with network identities:** devices that have a MAC address or a configuration
  URL in the device registry (MAC shown truncated).
- **Hosts.** Describe hosts yourself (name, optional MAC, optional hostname, IP addresses).
  - IP addresses are **not** an identity: when DHCP gives a new IP, you edit the host and it
    stays the same host.
  - MAC and hostname are identity keys and must be unique; if another host already uses them,
    you get a conflict, never an automatic merge.
  - Sentinel **suggests** matching HA devices (same MAC: strong; same hostname or IP: weak),
    but only you **link** them. A linked host appears on the [[Map|Panel Map]] as something
    the device depends on.

At level N0 Sentinel opens no network connection at all.

## Level N1 (opt-in): approved probes

Enable *Active network probes* in the options. Then approve targets one by one:

- Kinds: **TCP** (port open), **HTTP** and **HTTPS** (TLS certificates are always verified;
  no option to skip).
- Only addresses inside the **allowed ranges** (private networks by default). Cloud metadata
  addresses, link-local, multicast and broadcast are always blocked. Loopback needs an
  explicit permission per target.
- Names are resolved and the address is checked **after every DNS lookup**, then Sentinel
  connects to that exact address, which protects against DNS rebinding. Redirects are not
  followed; responses are capped.
- Limits: 2 s connect timeout (3 s total for HTTP), at most 4 probes at a time, 30 new
  connections per minute, one probe per target per minute. **Check now** uses the same
  limits.

Results:

| Result | Meaning |
|---|---|
| reachable | The service answered |
| service closed | The host refused the connection: it is up, but the service is not listening |
| unknown / filtered | Timeout: not the same as a network failure |
| unreachable | 3 failures in a row: **NET001**, closed after 2 successes |

A target can be linked to an HA device or to a host; then an unreachable endpoint becomes
direct evidence for the probable-cause ranking.

Sentinel never scans address ranges and never turns discovered services into probe targets
by itself.
