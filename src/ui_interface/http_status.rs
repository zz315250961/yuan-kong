//! One-shot results for uniquely scoped account/device HTTP requests.
//!
//! Pending results and legacy URL caches retain their existing read semantics.
//! A destroyed engine that never polls its final result can still leave one
//! entry behind; this is not a TTL or a general HTTP cache eviction policy.

use std::collections::HashMap;

fn is_account_device_request(url: &str) -> bool {
    let without_fragment = url.split_once('#').map_or(url, |(base, _)| base);
    let Some((_, query)) = without_fragment.split_once('?') else {
        return false;
    };
    query.split('&').any(|parameter| {
        matches!(
            parameter.split_once('='),
            Some(("account_device_request", value)) if !value.is_empty()
        )
    })
}

/// Read under the caller's existing mutex, consuming only completed results
/// belonging to the explicit account/device namespace. The exact single-space
/// sentinel is still pending; errors and empty responses are terminal too.
pub(crate) fn read_status(
    statuses: &mut HashMap<String, String>,
    url: &str,
) -> Option<String> {
    if is_account_device_request(url)
        && statuses.get(url).map_or(false, |value| value != " ")
    {
        statuses.remove(url)
    } else {
        statuses.get(url).cloned()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const URL: &str = "https://example.invalid/api/heartbeat?account_device_request=scope%3A1";

    fn statuses(value: &str) -> HashMap<String, String> {
        HashMap::from([(URL.to_owned(), value.to_owned())])
    }

    #[test]
    fn missing_result_stays_missing_without_insertion() {
        let mut cache = HashMap::new();
        assert_eq!(read_status(&mut cache, URL), None);
        assert!(cache.is_empty());
    }

    #[test]
    fn pending_survives_repeated_polls_until_completion() {
        let mut cache = statuses(" ");
        for _ in 0..5 {
            assert_eq!(read_status(&mut cache, URL).as_deref(), Some(" "));
            assert_eq!(cache.len(), 1);
        }
        cache.insert(URL.to_owned(), "done".to_owned());
        assert_eq!(read_status(&mut cache, URL).as_deref(), Some("done"));
        assert!(cache.is_empty());
    }

    #[test]
    fn completed_success_is_consumed_exactly_once() {
        let mut cache = statuses(r#"{"status_code":200,"body":"ok"}"#);
        assert!(read_status(&mut cache, URL).is_some());
        assert_eq!(read_status(&mut cache, URL), None);
        assert!(cache.is_empty());
    }

    #[test]
    fn completed_error_is_consumed_too() {
        let mut cache = statuses("request timed out");
        assert_eq!(read_status(&mut cache, URL).as_deref(), Some("request timed out"));
        assert!(cache.is_empty());
    }

    #[test]
    fn empty_and_non_sentinel_whitespace_are_terminal() {
        for value in ["", "  ", "\n"] {
            let mut cache = statuses(value);
            assert_eq!(read_status(&mut cache, URL).as_deref(), Some(value));
            assert!(cache.is_empty());
        }
    }

    #[test]
    fn legacy_results_remain_repeatably_readable() {
        for url in ["https://example.invalid/api/peers", "https://example.invalid/api/peers?other=1"] {
            let mut cache = HashMap::from([(url.to_owned(), "done".to_owned())]);
            for _ in 0..3 {
                assert_eq!(read_status(&mut cache, url).as_deref(), Some("done"));
            }
            assert_eq!(cache.len(), 1);
        }
    }

    #[test]
    fn lookalike_keys_values_fragments_and_empty_nonce_do_not_consume() {
        for url in [
            "https://example.invalid/account_device_request=scope",
            "https://example.invalid/api/peers?other=account_device_request=scope",
            "https://example.invalid/api/peers?not_account_device_request=scope",
            "https://example.invalid/api/peers?account_device_request_suffix=scope",
            "https://example.invalid/api/peers#?account_device_request=scope",
            "https://example.invalid/api/peers?other=1#&account_device_request=scope",
            "https://example.invalid/api/peers?account_device_request=",
            "https://example.invalid/api/peers?account_device_request",
        ] {
            let mut cache = HashMap::from([(url.to_owned(), "done".to_owned())]);
            assert_eq!(read_status(&mut cache, url).as_deref(), Some("done"));
            assert_eq!(cache.len(), 1, "legacy URL must not be consumed: {url}");
        }
    }

    #[test]
    fn actual_nonempty_query_key_matches_in_each_parameter_position() {
        for url in [
            "https://example.invalid/api/peers?account_device_request=scope&other=1",
            "https://example.invalid/api/peers?first=1&account_device_request=scope&last=2",
            "https://example.invalid/api/peers?first=1&account_device_request=scope%3A1#fragment",
        ] {
            let mut cache = HashMap::from([(url.to_owned(), "done".to_owned())]);
            assert_eq!(read_status(&mut cache, url).as_deref(), Some("done"));
            assert!(cache.is_empty());
        }
    }

    #[test]
    fn ten_thousand_unique_completed_heartbeats_leave_no_entries() {
        let mut cache = HashMap::new();
        for serial in 0..10_000 {
            let url = format!("https://example.invalid/api/heartbeat?account_device_request=scope%3A{serial}");
            cache.insert(url.clone(), " ".to_owned());
            assert_eq!(read_status(&mut cache, &url).as_deref(), Some(" "));
            cache.insert(url.clone(), "done".to_owned());
            assert_eq!(read_status(&mut cache, &url).as_deref(), Some("done"));
        }
        assert!(cache.is_empty());
    }
}
