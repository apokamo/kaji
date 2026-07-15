# Starter snapshot {{ starter_tag }}

## 対応 kaji Release

{{ kaji_release_url }}（`{{ target_kaji_release }}`）

## 反映内容

{{ applied_changes }}

## N/A とした変更と理由

{{ not_applicable_changes }}

## BREAKING 対応

{{ breaking_change_migrations }}

## 検証 evidence

- review PASS: {{ review_comment_url }}
- candidate SHA: `{{ candidate_sha }}`
- quality gate: {{ quality_gate_result }}

## snapshot の利用方法

この tag / GitHub Release は対応 kaji version を示す保守・監査 marker です。利用者は repository の
`Use this template` から default branch の current template を使います。過去 tag 専用の bootstrap
導線や生成済み consumer repository の update channel ではありません。
