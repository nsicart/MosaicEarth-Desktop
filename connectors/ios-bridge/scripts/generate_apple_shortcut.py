#!/usr/bin/env python3
"""Generate deterministic Apple Shortcut (`Send_to_MosaicEarth.shortcut`) for iOS Share Sheet."""
import plistlib
from pathlib import Path


def build_shortcut_workflow_dict() -> dict:
    tok_action_uuid = "5A1E0400-4E7A-4C92-8A10-000000000001"
    upload_action_uuid = "5A1E0400-4E7A-4C92-8A10-000000000002"
    url_val_uuid = "5A1E0400-4E7A-4C92-8A10-000000000003"

    return {
        "WFWorkflowClientVersion": "2605.0.5",
        "WFWorkflowMinimumClientVersionString": "900",
        "WFWorkflowMinimumClientVersion": 900,
        "WFWorkflowIcon": {
            "WFWorkflowIconStartColor": 2580427007,
            "WFWorkflowIconGlyphNumber": 61440,
        },
        "WFWorkflowTypes": ["ActionExtension", "NCWidget"],
        "WFWorkflowInputContentItemClasses": [
            "WFGenericFileContentItem",
            "WFImageContentItem",
            "WFPhotoMediaContentItem",
            "WFAVAssetContentItem",
            "WFURLContentItem",
        ],
        "WFWorkflowImportQuestions": [
            {
                "ActionIndex": 0,
                "Category": "Parameter",
                "ParameterKey": "WFTextActionText",
                "Text": "Paste your MosaicEarth Bridge Token (tap 'Get Shortcut Token' at https://app.mosaicearth.app/bridge):",
                "DefaultValue": "",
            }
        ],
        "WFWorkflowActions": [
            {
                "WFWorkflowActionIdentifier": "is.workflow.actions.gettext",
                "WFWorkflowActionParameters": {
                    "UUID": tok_action_uuid,
                    "WFTextActionText": "PASTE_MOSAICEARTH_BRIDGE_TOKEN_HERE",
                },
            },
            {
                "WFWorkflowActionIdentifier": "is.workflow.actions.downloadurl",
                "WFWorkflowActionParameters": {
                    "UUID": upload_action_uuid,
                    "WFURL": "https://app.mosaicearth.app/api/v2/bridge/quick-upload",
                    "WFHTTPMethod": "POST",
                    "WFHTTPBodyType": "Form",
                    "ShowHeaders": True,
                    "WFHTTPHeaders": {
                        "Value": {
                            "WFDictionaryFieldValueItems": [
                                {
                                    "WFItemType": 0,
                                    "WFKey": {
                                        "Value": {"string": "Authorization"},
                                        "WFSerializationType": "WFTextTokenString",
                                    },
                                    "WFValue": {
                                        "Value": {
                                            "string": "Bearer \ufffc",
                                            "attachmentsByRange": {
                                                "{7, 1}": {
                                                    "OutputName": "Text",
                                                    "OutputUUID": tok_action_uuid,
                                                    "Type": "ActionOutput",
                                                }
                                            },
                                        },
                                        "WFSerializationType": "WFTextTokenString",
                                    },
                                }
                            ]
                        },
                        "WFSerializationType": "WFDictionaryFieldValue",
                    },
                    "WFFormValues": {
                        "Value": {
                            "WFDictionaryFieldValueItems": [
                                {
                                    "WFItemType": 2,
                                    "WFKey": {
                                        "Value": {"string": "file"},
                                        "WFSerializationType": "WFTextTokenString",
                                    },
                                    "WFValue": {
                                        "Value": {"Type": "ExtensionInput"},
                                        "WFSerializationType": "WFTextTokenAttachment",
                                    },
                                }
                            ]
                        },
                        "WFSerializationType": "WFDictionaryFieldValue",
                    },
                },
            },
            {
                "WFWorkflowActionIdentifier": "is.workflow.actions.getvalueforkey",
                "WFWorkflowActionParameters": {
                    "UUID": url_val_uuid,
                    "WFDictionaryKey": "share_url",
                    "WFInput": {
                        "Value": {
                            "OutputName": "Contents of URL",
                            "OutputUUID": upload_action_uuid,
                            "Type": "ActionOutput",
                        },
                        "WFSerializationType": "WFTextTokenAttachment",
                    },
                },
            },
            {
                "WFWorkflowActionIdentifier": "is.workflow.actions.setclipboard",
                "WFWorkflowActionParameters": {
                    "WFInput": {
                        "Value": {
                            "OutputName": "Dictionary Value",
                            "OutputUUID": url_val_uuid,
                            "Type": "ActionOutput",
                        },
                        "WFSerializationType": "WFTextTokenAttachment",
                    }
                },
            },
            {
                "WFWorkflowActionIdentifier": "is.workflow.actions.openurl",
                "WFWorkflowActionParameters": {
                    "WFInput": {
                        "Value": {
                            "OutputName": "Dictionary Value",
                            "OutputUUID": url_val_uuid,
                            "Type": "ActionOutput",
                        },
                        "WFSerializationType": "WFTextTokenAttachment",
                    }
                },
            },
        ],
    }


def main() -> None:
    out_dir = Path(__file__).resolve().parents[1] / "Shortcut"
    out_dir.mkdir(parents=True, exist_ok=True)
    shortcut_path = out_dir / "Send_to_MosaicEarth.shortcut"
    xml_preview_path = out_dir / "Send_to_MosaicEarth.plist.xml"
    wf = build_shortcut_workflow_dict()
    shortcut_path.write_bytes(plistlib.dumps(wf, fmt=plistlib.FMT_BINARY, sort_keys=True))
    xml_preview_path.write_bytes(plistlib.dumps(wf, fmt=plistlib.FMT_XML, sort_keys=True))


if __name__ == "__main__":
    main()
