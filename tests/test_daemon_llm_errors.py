"""Tests fiche #36 (02/09) : résilience du daemon aux erreurs LLM + logs UTF-8.

La panne du 25/08 (400 « temperature » sur claude-sonnet-5) a arrêté le daemon
47 fois : chaque ModelHTTPError était traité comme fatal, emportant aussi les
étages sans LLM (connects, drain de la file). Et le message d'alerte n'a jamais
été écrit en clair dans daemon.log (UnicodeEncodeError cp1252 sur la flèche →).
"""
from __future__ import annotations

import logging

from linkedin.daemon import LLM_ERROR_STREAK_ALERT, llm_error_action
from linkedin.logging import force_utf8


class TestLlmErrorAction:
    def test_400_continue(self):
        """Un 400 (paramètre refusé) ne doit JAMAIS arrêter le daemon."""
        assert llm_error_action(400) == "continue"

    def test_auth_stop(self):
        assert llm_error_action(401) == "stop"
        assert llm_error_action(403) == "stop"

    def test_transitoires_backoff(self):
        assert llm_error_action(429) == "backoff"
        assert llm_error_action(500) == "backoff"
        assert llm_error_action(529) == "backoff"

    def test_status_inconnu_continue(self):
        """Pas de status (erreur réseau enveloppée) : on continue, le retry-cap
        des tasks borne la boucle."""
        assert llm_error_action(None) == "continue"
        assert llm_error_action(404) == "continue"

    def test_seuil_alerte_raisonnable(self):
        assert 3 <= LLM_ERROR_STREAK_ALERT <= 10


class TestForceUtf8:
    def test_stream_cp1252_reconfigure_et_ecrit_la_fleche(self, tmp_path):
        """Reproduit la panne : stream en cp1252, message avec →."""
        p = tmp_path / "daemon.log"
        stream = open(p, "w", encoding="cp1252")
        try:
            assert force_utf8(stream) is True
            stream.write("Daemon stopped → LLM API error ▶ détail\n")
        finally:
            stream.close()
        assert "→" in p.read_text(encoding="utf-8")

    def test_logging_handler_sur_stream_reconfigure(self, tmp_path):
        """Un StreamHandler branché après force_utf8 écrit les flèches en clair
        (avant : bloc '--- Logging error ---' à la place du message)."""
        p = tmp_path / "daemon.log"
        stream = open(p, "w", encoding="cp1252")
        force_utf8(stream)
        handler = logging.StreamHandler(stream)
        test_logger = logging.getLogger("test_fiche36")
        test_logger.addHandler(handler)
        test_logger.setLevel(logging.ERROR)
        try:
            test_logger.error("alerte avec fleche → et triangle ▶")
        finally:
            test_logger.removeHandler(handler)
            handler.close()
        content = p.read_text(encoding="utf-8")
        assert "alerte avec fleche → et triangle ▶" in content
        assert "Logging error" not in content

    def test_objet_sans_reconfigure_retourne_false(self):
        assert force_utf8(object()) is False
