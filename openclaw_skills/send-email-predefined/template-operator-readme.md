# Template Operator README

The predefined skill ships with a sample `templates.json` file next to the skill. Its shape is:

```json
{
  "templates": {
    "ops-alert": {
      "account": "primary",
      "subject": "Alert: {title}",
      "text_body": "Severity: {severity}\n\n{summary}",
      "html_body": "<p><strong>Severity:</strong> {severity}</p><p>{summary}</p>",
      "to": ["ops@example.com"],
      "cc": [],
      "bcc": [],
      "allowed_params": ["severity", "summary", "title"]
    }
  }
}
```

Rules:

- `account` is required and must be a Mail Sentry account name
- `subject` is required
- at least one of `text_body` or `html_body` is required
- `to` is required and must be a non-empty array
- `allowed_params` is optional; if omitted, all placeholders used by the template are allowed

In the installed OpenClaw layout, the default location is typically:

```text
/home/node/.openclaw/skills/send-email-predefined/templates.json
```

Template notes:

- `ops-alert` is a tightly structured operational example where the caller fills in a few specific fields.
- `personal-followup` is intentionally looser. In that template, `{body}` is expected to come from the agent at send time when it prepares the follow-up message body, while the template still fixes the account and overall field shape.
