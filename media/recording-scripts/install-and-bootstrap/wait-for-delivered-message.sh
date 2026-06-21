message_found=1
for _attempt in $(seq 1 20); do
  if arbiter arbiter.url="$ARBITER_CINEMA_STAGING_URL" op run imap:search_messages --args '{"account":"bot","folder":"INBOX","query":"Arbiter install smoke test","limit":1}' | jq -er '.result.messages[0].uid' >/dev/null; then
    message_found=0
    break
  fi
  sleep 0.25
done
if [[ "$message_found" != 0 ]]; then
  arbiter arbiter.url="$ARBITER_CINEMA_STAGING_URL" op run imap:search_messages --args '{"account":"bot","folder":"INBOX","query":"Arbiter install smoke test","limit":1}' | jq .
fi
[[ "$message_found" == 0 ]]
