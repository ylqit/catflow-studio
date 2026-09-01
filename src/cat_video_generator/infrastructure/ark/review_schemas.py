"""Ark Responses 视觉审核使用的严格结构化输出 Schema。"""

IMAGE_DIAGNOSTIC_SCHEMA = {
    "type": "object",
    "properties": {
        "identityOk": {"type": "boolean"},
        "identityAssessment": {
            "type": "string",
            "enum": ["consistent", "uncertain", "mismatch"],
        },
        "styleOk": {"type": "boolean"},
        "constraintsOk": {"type": "boolean"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "violations": {"type": "array", "items": {"type": "string"}},
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "object": {"type": "string"},
                    "observation": {"type": "string"},
                    "relationError": {"type": ["string", "null"]},
                },
                "required": ["object", "observation", "relationError"],
                "additionalProperties": False,
            },
        },
    },
    "required": [
        "identityOk",
        "identityAssessment",
        "styleOk",
        "constraintsOk",
        "confidence",
        "violations",
        "evidence",
    ],
    "additionalProperties": False,
}

VIDEO_DIAGNOSTIC_SCHEMA = {
    "type": "object",
    "properties": {
        "identityOk": {"type": "boolean"},
        "styleOk": {"type": "boolean"},
        "constraintsOk": {"type": "boolean"},
        "narrativeOrderOk": {"type": "boolean"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "violations": {"type": "array", "items": {"type": "string"}},
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "timestamp": {"type": "string"},
                    "object": {"type": "string"},
                    "observation": {"type": "string"},
                    "relationError": {"type": ["string", "null"]},
                },
                "required": ["timestamp", "object", "observation", "relationError"],
                "additionalProperties": False,
            },
        },
        "shotBoundariesSeconds": {
            "type": "array",
            "items": {"type": "number", "minimum": 0, "maximum": 45},
            "maxItems": 4,
        },
    },
    "required": [
        "identityOk",
        "styleOk",
        "constraintsOk",
        "narrativeOrderOk",
        "confidence",
        "violations",
        "evidence",
        "shotBoundariesSeconds",
    ],
    "additionalProperties": False,
}
