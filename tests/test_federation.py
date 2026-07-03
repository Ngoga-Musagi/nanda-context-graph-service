"""Tests for federation sync — jurisdiction filter and peer management.

The jurisdiction_filter tests run without Neo4j (pure logic).
"""

import pytest

from federation.sync import jurisdiction_filter, FederationPeer


class TestJurisdictionFilter:
    """Test jurisdiction-gated federation sync rules."""

    def test_no_jurisdiction_allows_all(self):
        """Traces without a jurisdiction field sync to any peer."""
        trace = {"trace_id": "t1", "agent_id": "a1"}
        assert jurisdiction_filter(trace, "US") is True
        assert jurisdiction_filter(trace, "EU") is True
        assert jurisdiction_filter(trace, None) is True

    def test_global_peer_allows_all(self):
        """A peer with jurisdiction='global' or None accepts all traces."""
        trace = {"trace_id": "t1", "agent_id": "a1", "jurisdiction": "EU"}
        assert jurisdiction_filter(trace, "global") is True
        assert jurisdiction_filter(trace, None) is True

    def test_eu_trace_to_eu_peer_allowed(self):
        """EU trace syncs to EU peer."""
        trace = {"trace_id": "t1", "jurisdiction": "EU"}
        assert jurisdiction_filter(trace, "EU") is True

    def test_eu_trace_to_eea_peer_allowed(self):
        """EU trace syncs to EEA peer (equivalent jurisdiction)."""
        trace = {"trace_id": "t1", "jurisdiction": "EEA"}
        assert jurisdiction_filter(trace, "EEA") is True

    def test_eu_trace_to_us_peer_blocked(self):
        """EU trace must NOT sync to US peer (GDPR data residency)."""
        trace = {"trace_id": "t1", "jurisdiction": "EU"}
        assert jurisdiction_filter(trace, "US") is False

    def test_eu_trace_to_apac_peer_blocked(self):
        """EU trace must NOT sync to APAC peer."""
        trace = {"trace_id": "t1", "jurisdiction": "EU"}
        assert jurisdiction_filter(trace, "APAC") is False

    def test_us_trace_to_eu_peer_blocked(self):
        """US trace does not match EU peer jurisdiction."""
        trace = {"trace_id": "t1", "jurisdiction": "US"}
        assert jurisdiction_filter(trace, "EU") is False

    def test_exact_match_non_eu(self):
        """Non-EU jurisdictions match exactly."""
        trace = {"trace_id": "t1", "jurisdiction": "US"}
        assert jurisdiction_filter(trace, "US") is True

    def test_case_sensitive(self):
        """Jurisdiction matching is case-sensitive per spec."""
        trace = {"trace_id": "t1", "jurisdiction": "EU"}
        assert jurisdiction_filter(trace, "eu") is False


class TestFederationPeer:
    def test_peer_defaults(self):
        peer = FederationPeer(url="http://peer1:7201/")
        assert peer.url == "http://peer1:7201"
        assert peer.name == "http://peer1:7201"
        assert peer.jurisdiction is None
        assert peer.healthy is True

    def test_peer_with_jurisdiction(self):
        peer = FederationPeer(url="http://eu-peer:7201", jurisdiction="EU")
        assert peer.jurisdiction == "EU"
