ALTER TABLE wechat_interpretations
ADD COLUMN criteria_reason_source TEXT
CHECK (
  criteria_reason_source IS NULL
  OR criteria_reason_source IN ('json', 'markdown_value_judgment_line')
);

ALTER TABLE wechat_interpretations
ADD COLUMN interpret_user TEXT;
