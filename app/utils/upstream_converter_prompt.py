"""Prompt builder for downstream-to-upstream driver conversions."""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List


SYSTEM_PROMPT = (
    "You are an expert Linux kernel upstream engineer specializing in ASoC audio\n"
    "drivers (sound/soc/qcom/, sound/soc/codecs/). You convert Qualcomm downstream\n"
    "kernel drivers into upstream-ready patches following kernel.org submission\n"
    "standards. You know the upstream maintainers' preferences, checkpatch rules,\n"
    "Documenting Device Trees requirements, and ALSA/ASoC subsystem conventions."
)


def _safe(value: str | None, fallback: str = "N/A") -> str:
    text = (value or "").strip()
    return text if text else fallback


def build_conversion_prompt(payload: Dict) -> List[Dict[str, str]]:
    metadata = payload.get("metadata") or {}
    source_code = payload.get("source_code") or ""
    filename = payload.get("filename") or "driver.c"
    requirements = payload.get("requirements") or ""
    conversion_type = payload.get("conversion_type") or "full_upstream"
    target_kernel = payload.get("target_kernel") or "latest"

    def _base_user_message(code: str, note: str = "") -> str:
        author = _safe(metadata.get("author"))
        return (
            "## Source Information\n"
            f"- CL Number: {_safe(metadata.get('cl_number'))}\n"
            f"- CL Subject: {_safe(metadata.get('subject'))}\n"
            f"- Author: {author}\n"
            f"- Repository: {_safe(metadata.get('repo'))}\n"
            f"- File Path: {_safe(metadata.get('file_path')) if metadata.get('file_path') else filename}\n"
            "- Original Description:\n"
            f"  {_safe(metadata.get('description'))}\n\n"
            "## Conversion Requirements / Intent\n"
            f"{requirements if requirements else 'Convert to upstream-compatible format following standard kernel guidelines.'}\n\n"
            "## Task\n"
            "Convert the following downstream Qualcomm kernel driver to an upstream-ready\n"
            "Linux kernel patch. Apply these rules:\n\n"
            "MANDATORY TRANSFORMATIONS:\n"
            "1. Remove all Qualcomm-internal headers (#include <soc/qcom/...>, <linux/msm_...>)\n"
            "   → Replace with upstream equivalents or remove if unused\n"
            "2. Remove QCOM_SPECIFIC ifdefs — upstream uses DT properties instead\n"
            "3. Replace downstream regmap wrappers → use upstream regmap API directly\n"
            "4. Replace downstream GPIO APIs → use gpiod_* APIs\n"
            "5. Replace downstream clock APIs → use clk_get/clk_prepare_enable\n"
            "6. Remove QDSP6/APR/ADSP IPC calls → these don't belong in upstream codec drivers\n"
            "7. Fix all checkpatch.pl --strict violations (spacing, 80-char lines, etc.)\n"
            "8. Add SPDX license header: // SPDX-License-Identifier: GPL-2.0-only\n"
            "9. Update MODULE_LICENSE('GPL v2') → MODULE_LICENSE('GPL')\n"
            "10. Convert platform_data structs → DT-based probe using of_device_id table\n"
            "11. Add proper .of_match_table with compatible strings like 'qcom,wcd937x'\n"
            "12. Ensure snd_soc_component_driver uses upstream callback names\n"
            "13. Remove msm_* prefix from exported symbols → use qcom_* or device-specific names\n\n"
            "OUTPUT FORMAT:\n"
            "Provide a complete git format-patch style patch:\n"
            f"- From: {author} <email@domain.com>\n"
            f"- Date: {datetime.utcnow().strftime('%Y-%m-%d')}\n"
            f"- Subject: [PATCH] ASoC: codecs: {filename.rsplit('.', 1)[0]}: <short description>\n"
            "- Proper commit message body with Signed-off-by\n"
            f"- Full unified diff (--- a/sound/soc/codecs/{filename} +++ b/sound/soc/codecs/{filename})\n\n"
            "Also provide after the patch:\n"
            "## CONVERSION SUMMARY\n"
            "- Files modified: [list]\n"
            "- Downstream APIs removed: [list]\n"
            "- Upstream replacements used: [list]\n"
            "- DT bindings needed: [yes/no + what]\n"
            "- Checkpatch issues fixed: [count]\n"
            "- Known issues requiring manual review: [list]\n"
            "- Suggested maintainer/mailing list: [e.g. alsa-devel@alsa-project.org]\n"
            f"\nTarget kernel: {target_kernel} | Conversion type: {conversion_type}\n"
            + (f"\n{note}\n" if note else "")
            + "\n## Driver Source Code (" + filename + ")\n"
            + "```c\n" + code + "\n```\n"
        )

    trimmed_source = source_code[:50000]
    user_message = _base_user_message(trimmed_source)
    if len(user_message) > 80000:
        note = "SOURCE TRUNCATED"
        base_without_source = _base_user_message("")
        available = max(0, 80000 - len(base_without_source) - len(note) - 20)
        trimmed_source = source_code[:available]
        user_message = _base_user_message(trimmed_source, note=note)

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]
