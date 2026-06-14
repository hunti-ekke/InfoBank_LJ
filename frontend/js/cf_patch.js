window.actionListChatResponse = async function(q) {
    const r = await fetch(`${API}/evidence/action-list`, { headers: authHeaders() });
    const data = await readApiResponse(r);
    const units = data.classified_units || [];
    const openItems = data.open_items || [];
    const controlledFailure = data.controlled_failure || (data.controlled_failures || [])[0] || null;
    let answer = formatActionListFromEvidence(data);
    if (!openItems.length && controlledFailure?.safe_output) answer = controlledFailure.safe_output;
    return {
        status: (!openItems.length && controlledFailure) ? 'controlled_failure' : 'success',
        controlled_failure: controlledFailure,
        reason: controlledFailure?.reason || null,
        message: controlledFailure?.message || answer,
        answer,
        extracted_keywords: ['action', 'email', 'calendar'],
        query_profile: {
            lexical_terms: q.toLowerCase().split(/\W+/).filter(Boolean).slice(0, 12),
            semantic_tags: ['action', 'email', 'calendar'],
            task_intent: 'current_action_list',
            retrieval_strategy: 'evidence_reconstruction',
            expected_genres: ['email_or_message', 'calendar_or_schedule', 'activity_trace'],
        },
        governance: {
            source_type: 'evidence_units',
            usable_doc_ids: data.policy?.usable_unit_ids || [],
            denied_doc_ids: data.policy?.denied_unit_ids || [],
            metadata_only_doc_ids: data.policy?.metadata_only_unit_ids || [],
        },
        source_role_summary: evidenceRoleSummary(units),
        relevance_level_summary: evidenceLevelSummary(units),
        evidence_check: {
            decision: openItems.length ? 'answer_allowed' : (controlledFailure ? 'controlled_failure' : 'no_open_items'),
            counts: data.counts || {},
            controlled_failures: data.controlled_failures || [],
        },
        sources: evidenceSources(units),
    };
};