import socket
from dnslib import DNSRecord, RCODE
from rule_engine import check_access, is_netflix_domain

UPSTREAM_HOST = '8.8.8.8'
UPSTREAM_PORT = 53


def _upstream(data: bytes) -> bytes | None:
    """Forward a raw DNS query to Google's resolver."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(3)
        sock.sendto(data, (UPSTREAM_HOST, UPSTREAM_PORT))
        response, _ = sock.recvfrom(4096)
        sock.close()
        return response
    except Exception as e:
        print(f'[upstream] {e}')
        return None


def handle_query(dns_data: bytes, token: str) -> bytes | None:
    """
    Parse the DoH request, apply rules, return raw DNS response bytes.
    Returns None on unrecoverable error.
    """
    try:
        req    = DNSRecord.parse(dns_data)
        domain = str(req.q.qname).rstrip('.')

        if is_netflix_domain(domain):
            allowed, reason = check_access(domain, token)
            if not allowed:
                # Return NXDOMAIN — domain "doesn't exist" from device's view
                reply = req.reply()
                reply.header.rcode = RCODE.NXDOMAIN
                print(f'[block] {domain} → {reason} (token={token[:8]}...)')
                return reply.pack()

        # Pass through to upstream for everything allowed / non-Netflix
        return _upstream(dns_data)

    except Exception as e:
        print(f'[dns_resolver] {e}')
        try:
            req   = DNSRecord.parse(dns_data)
            reply = req.reply()
            reply.header.rcode = RCODE.SERVFAIL
            return reply.pack()
        except Exception:
            return None
