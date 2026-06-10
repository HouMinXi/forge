



































LOGIN_RETRIES=5                       # max login attempts per refresh cycle
LOGIN_RETRY_DELAY=3                   # seconds between login retries
HEARTBEAT_INTERVAL=600                # seconds between periodic "VPN healthy" log entries (0=off)
TRANSIENT_THRESHOLD=4                 # consecutive external timeouts (local state OK) before escalating to fail_count
# Auto-detect WiFi interface; fallback to wlp9s0f0 if iw is unavailable
WIFI_INTERFACE=$(iw dev 2>/dev/null | awk '/Interface/{print $2; exit}')
[ -z "$WIFI_INTERFACE" ] && WIFI_INTERFACE="wlp9s0f0"





































































































































#   <country>    -- country probe returned a country code (non-empty)
#   ""           -- all external probes timed out (but local state was OK = transient)
# "CN" is a valid country code return (VPN up but routing via China = broken exit).
check_vpn_health() {
	# Layer 1: local state -- deterministic, milliseconds, no network dependency
	if ! check_vpn_local_state; then

		return
	fi

	# Layer 2: parallel external probes -- all four run concurrently, max wait = one timeout
	local tmp_g tmp_cf tmp_ifc tmp_ipa r_g r_cf r_ifc r_ipa r_country
	tmp_g=$(mktemp /tmp/surflare_hc.XXXXXX)
	tmp_cf=$(mktemp /tmp/surflare_hc.XXXXXX)
	tmp_ifc=$(mktemp /tmp/surflare_hc.XXXXXX)
	tmp_ipa=$(mktemp /tmp/surflare_hc.XXXXXX)
	# Ensure temp files are removed even if this function is interrupted mid-wait.
	# Stored in a global so the main EXIT trap can also clean up on unclean exit.
	_hc_tmp="$tmp_g $tmp_cf $tmp_ifc $tmp_ipa"

	# Google: blocked by GFW -> 200/30x means VPN is working
	(
		code=$(curl -s --connect-timeout 3 --max-time 8 \
		       -o /dev/null -w '%{http_code}' https://www.google.com 2>/dev/null)
		case "$code" in 200|301|302) echo "OK" ;; esac
	) >"$tmp_g" 2>/dev/null &
	local pid_g=$!

	# Country probe A: Cloudflare trace (no rate limit, parse loc= field)
	(
		curl -s --connect-timeout 3 --max-time 8 \
		     'https://cloudflare.com/cdn-cgi/trace' 2>/dev/null \
		| awk -F= '/^loc=/{print $2}' | tr -d '[:space:]'
	) >"$tmp_cf" 2>/dev/null &
	local pid_cf=$!

	# Country probe B: ifconfig.co ISO country code (degrades gracefully on rate limit)
	(
		curl -s --connect-timeout 3 --max-time 8 \
		     'https://ifconfig.co/country-iso' 2>/dev/null \
		| tr -d '[:space:]'
	) >"$tmp_ifc" 2>/dev/null &
	local pid_ifc=$!

	# Country probe C: ipapi.co (1000 req/day free tier; degrades gracefully above limit)
	(
		curl -s --connect-timeout 3 --max-time 8 \
		     'https://ipapi.co/country/' 2>/dev/null \
		| tr -d '[:space:]'
	) >"$tmp_ipa" 2>/dev/null &
	local pid_ipa=$!

	wait "$pid_g" "$pid_cf" "$pid_ifc" "$pid_ipa"
	r_g=$(cat "$tmp_g" 2>/dev/null)
	r_cf=$(cat "$tmp_cf" 2>/dev/null)
	r_ifc=$(cat "$tmp_ifc" 2>/dev/null)
	r_ipa=$(cat "$tmp_ipa" 2>/dev/null)
	rm -f "$tmp_g" "$tmp_cf" "$tmp_ifc" "$tmp_ipa"
	_hc_tmp=""

	# Google result takes priority (most reliable GFW indicator)
	[ "$r_g" = "OK" ] && echo "OK" && return
	# Country probes: first valid 2-letter code wins (A->B->C priority)
	for r_country in "$r_cf" "$r_ifc" "$r_ipa"; do
		[[ "$r_country" =~ ^[A-Z]{2}$ ]] && echo "$r_country" && return
	done
	echo ""
}

wait_for_exit() {
























































	return "$rc"
}

cleanup_probe_state() {
	surflare disconnect >/dev/null 2>&1
	killall surflare-proxy 2>/dev/null



















































				continue
				;;
		esac
		local ms_int
		ms_int=$(awk "BEGIN {printf \"%.0f\", ${ms} * 1000}" 2>/dev/null)
		if [ -z "$ms_int" ] || [ "$ms_int" -le 0 ] 2>/dev/null; then
































































		local effective_transit="$TRANSIT"
		if [ -n "$TRANSIT_CANDIDATES" ] && [ -z "$TRANSIT" ]; then
			effective_transit=$(probe_best_transit)
			cleanup_probe_state
		fi

		log "Connecting to ${NODE} mode=${MODE:-global} transit=${effective_transit:-off} (daemon mode)..."






















































































































































































































				fail_count=0
				reconnect_count=0
				transient_count=0
			else
				reconnect_count=$((reconnect_count + 1))
				log "Post-reconnect health check anomalous (reconnect_count=${reconnect_count})"
				if [ "$reconnect_count" -ge "$STORM_MAX" ]; then
					log "Storm protection triggered: cooling for ${STORM_COOLING}s"
					sleep "$STORM_COOLING" &







			fi
		else
			reconnect_count=$((reconnect_count + 1))
			log "Reconnect attempt failed (reconnect_count=${reconnect_count})"
			if [ "$reconnect_count" -ge "$STORM_MAX" ]; then
				log "Storm protection triggered (connect failure): cooling for ${STORM_COOLING}s"
				sleep "$STORM_COOLING" &





