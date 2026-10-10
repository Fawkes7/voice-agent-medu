"""Unit tests for doctor attitude → SP emotion mapping (no API calls)."""

import unittest

from app.agents.doctor_attitude import (
    Attitude,
    ContentEmpathy,
    DoctorTurnAnalysis,
    EmotionalResponse,
    ResponseLevel,
    Certainty,
    analysis_to_sp_emotion,
    detect_emotion_shift_regex,
    _patient_context_from_history,
)


class TestDoctorAttitudeMapping(unittest.TestCase):
    def test_regex_empathetic(self):
        self.assertEqual(detect_emotion_shift_regex("别担心，慢慢说"), "empathetic")

    def test_regex_rushing(self):
        self.assertEqual(detect_emotion_shift_regex("赶紧，还有别的吗"), "rushing")

    def test_caring_attitude(self):
        analysis = DoctorTurnAnalysis(
            content_empathy=ContentEmpathy.EXPLICIT,
            content_evidence="理解你的担心",
            emotional_response=EmotionalResponse(
                patient_cue="害怕",
                judgment=ResponseLevel.FULL,
                evidence="安抚",
            ),
            overall_attitude=Attitude.CARING,
            certainty=Certainty.HIGH,
            limitations="",
        )
        self.assertEqual(analysis_to_sp_emotion(analysis), "empathetic")

    def test_dismissive_maps_cold(self):
        analysis = DoctorTurnAnalysis(
            content_empathy=ContentEmpathy.DISMISSIVE,
            content_evidence="别大惊小怪",
            emotional_response=EmotionalResponse(
                patient_cue="疼痛",
                judgment=ResponseLevel.DISMISSIVE,
                evidence="否定",
            ),
            overall_attitude=Attitude.NEUTRAL,
            certainty=Certainty.MEDIUM,
            limitations="",
        )
        self.assertEqual(analysis_to_sp_emotion(analysis), "cold")

    def test_patient_context_from_history(self):
        history = [
            {"role": "patient", "content": "我很怕"},
            {"role": "student", "content": "哪里疼？"},
        ]
        self.assertEqual(_patient_context_from_history(history), "我很怕")


if __name__ == "__main__":
    unittest.main()
