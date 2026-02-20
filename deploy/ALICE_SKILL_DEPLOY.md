# Alice Skill Deploy Runbook

## 1. Prerequisites

1. PostgreSQL migration `008_voice_account_linking.sql` must be applied in production.
2. `mcp_server_api_key` must be set in `infra/terraform.tfvars`.
3. `voice_link_api_key` must be set (same key in bot VM API and Alice Function env).
4. `alice_skill_id` must be set (exact `skill_id` from Yandex Dialogs).
5. `alice_function_network_id` must be set (production mode uses private VPC connectivity).
6. `python3` + `pip` must be available locally (build script assembles Linux-compatible wheels automatically).

## 2. Build function ZIP

```bash
cd /Users/denispukinov/Downloads/vkuswill_bot
make build-alice-zip
```

Expected artifact:

`/Users/denispukinov/Downloads/vkuswill_bot/dist/alice-skill.zip`

## 3. Enable function in Terraform

Set variables in `infra/terraform.tfvars`:

```hcl
alice_function_enabled        = true
alice_function_name           = "vkuswill-alice-skill"
alice_function_zip_path       = "../dist/alice-skill.zip"
alice_function_network_id     = "<VPC_NETWORK_ID>"
alice_link_api_url            = "" # empty -> auto http://<VM_INTERNAL_IP>:8080/voice-link
alice_skill_id                = "<DIALOGS_SKILL_ID>"
alice_linking_fail_closed     = true
alice_link_api_timeout_seconds = 5
alice_order_rate_limit        = 12
alice_order_rate_window_seconds = 60
alice_link_code_rate_limit    = 6
alice_link_code_rate_window_seconds = 600
alice_idempotency_ttl_seconds = 90
alice_db_connect_timeout_seconds = 3
alice_degrade_to_guest_on_db_error = false
voice_link_code_ttl_minutes   = 10
voice_link_api_key            = "..."
mcp_server_api_key            = "..."
```

`alice_link_api_url` можно не указывать: Terraform автоматически подставит internal URL VM для `/voice-link`.

## 4. Apply Terraform

```bash
cd /Users/denispukinov/Downloads/vkuswill_bot/infra
terraform init -backend-config=backend.conf
terraform apply
```

## 5. Configure Yandex Dialogs

1. Get invoke URL:

```bash
terraform output alice_function_invoke_url
```

2. Put this URL into Alice skill backend settings.
3. Verify function got expected voice-link URL:

```bash
terraform output alice_link_api_url_effective
```

## 6. Smoke test

1. In Telegram bot: `/link_voice` and copy one-time code.
2. In Alice skill: say `код 123456` (with your code).
3. Then say: `закажи молоко`.
4. Confirm voice answer + button with cart link.
