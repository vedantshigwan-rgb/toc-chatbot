from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Tuple

from utils.search_faiss_indexes import FAQIndexSearcher
from utils.variable_injector import (
    inject_variable_values,
    normalize_variables_key,
)


async def search_faqs(
    *,
    faq_searcher: FAQIndexSearcher,
    report_name: str,
    query_without_values: str,
    variable_names: List[str],
    top_k: int = 3,
) -> Dict[str, Any]:
    """
    Search strategy:

    1. First try the exact variable-combination index.
    2. If that exact index does not exist, fall back to the
       report-level `all` index.
    3. Other search errors are returned normally.
    """

    requested_variables_key = normalize_variables_key(
        variable_names
    )

    # ============================================================
    # ATTEMPT 1: Exact variable index
    # ============================================================

    try:
        exact_search_result = await asyncio.to_thread(
            faq_searcher.search,
            report_name=report_name,
            query_text=query_without_values,
            variable_names=variable_names,
            top_k=top_k,
            search_all_variables=False,
        )

    except Exception as error:
        return {
            "status": "error",
            "message": str(error),
            "results": [],
            "exception_type": type(error).__name__,
            "search_mode": "exact_variables",
            "fallback_used": False,
            "requested_variables_key": requested_variables_key,
        }

    if not isinstance(
        exact_search_result,
        dict,
    ):
        return {
            "status": "error",
            "message": (
                "FAQ search returned an "
                "invalid response type."
            ),
            "results": [],
            "response_type": type(
                exact_search_result
            ).__name__,
            "search_mode": "exact_variables",
            "fallback_used": False,
            "requested_variables_key": requested_variables_key,
        }

    # Exact search succeeded.
    if (
        exact_search_result.get("status")
        == "success"
    ):
        exact_search_result[
            "search_mode"
        ] = "exact_variables"

        exact_search_result[
            "fallback_used"
        ] = False

        exact_search_result[
            "requested_variables_key"
        ] = requested_variables_key

        return exact_search_result

    # ============================================================
    # Fall back ONLY when the exact variable index does not exist.
    # ============================================================

    exact_error_message = str(
        exact_search_result.get(
            "message",
            "",
        )
        or ""
    )

    exact_index_missing = (
        "No exact variable index exists"
        in exact_error_message
    )

    if not exact_index_missing:
        exact_search_result[
            "search_mode"
        ] = "exact_variables"

        exact_search_result[
            "fallback_used"
        ] = False

        exact_search_result[
            "requested_variables_key"
        ] = requested_variables_key

        return exact_search_result

    # ============================================================
    # ATTEMPT 2: Report-level common `all` index
    # ============================================================

    # Retrieve more than top_k because incompatible FAQ candidates
    # may be rejected later during preparation.
    fallback_top_k = max(
        top_k * 5,
        15,
    )

    try:
        fallback_search_result = await asyncio.to_thread(
            faq_searcher.search,
            report_name=report_name,
            query_text=query_without_values,
            variable_names=None,
            top_k=fallback_top_k,
            search_all_variables=True,
        )

    except Exception as error:
        return {
            "status": "error",
            "message": str(error),
            "results": [],
            "exception_type": type(error).__name__,
            "search_mode": "report_all_fallback",
            "fallback_used": True,
            "requested_variables_key": requested_variables_key,
            "exact_search_error": exact_error_message,
        }

    if not isinstance(
        fallback_search_result,
        dict,
    ):
        return {
            "status": "error",
            "message": (
                "Fallback FAQ search returned "
                "an invalid response type."
            ),
            "results": [],
            "response_type": type(
                fallback_search_result
            ).__name__,
            "search_mode": "report_all_fallback",
            "fallback_used": True,
            "requested_variables_key": requested_variables_key,
            "exact_search_error": exact_error_message,
        }

    fallback_search_result[
        "search_mode"
    ] = "report_all_fallback"

    fallback_search_result[
        "fallback_used"
    ] = True

    fallback_search_result[
        "requested_variables_key"
    ] = requested_variables_key

    fallback_search_result[
        "exact_search_error"
    ] = exact_error_message

    return fallback_search_result


def _variables_key_to_set(
    variables_key: str,
) -> set[str]:
    """
    Convert a canonical FAISS variables key to a set.

    Examples:
        "buyer_name,item"
            -> {"buyer_name", "item"}

        "no_variables"
            -> set()
    """

    cleaned_key = str(
        variables_key or ""
    ).strip().lower()

    if (
        not cleaned_key
        or cleaned_key == "no_variables"
    ):
        return set()

    return {
        variable.strip()
        for variable
        in cleaned_key.split(",")
        if variable.strip()
    }


def prepare_faq_examples(
    *,
    search_result: Dict[str, Any],
    report_name: str,
    expected_variables_key: str,
    variable_values: Dict[str, Any],
    top_k: int = 3,
) -> Tuple[
    List[Dict[str, Any]],
    List[Dict[str, Any]],
]:
    """
    Validate FAISS results and prepare few-shot SQL examples.

    Normal exact-index mode:
        The FAQ result must match the exact requested
        variable combination.

    Report-level fallback mode:
        The FAQ candidate may require fewer variables than
        the request, but every variable required by that FAQ
        must be present in the request.

        Example:
            request variables:
                business_group, oa_number

            FAQ variables:
                oa_number

            This is valid. `business_group` can later be
            classified as an extra filter variable.

    Returns:
        prepared_results:
            Valid FAQ examples ready for SQL generation.

        rejected_results:
            FAQ results rejected during validation/preparation.
    """

    prepared_results: List[
        Dict[str, Any]
    ] = []

    rejected_results: List[
        Dict[str, Any]
    ] = []

    raw_results = search_result.get(
        "results",
        [],
    )

    fallback_used = bool(
        search_result.get(
            "fallback_used",
            False,
        )
    )

    supplied_variable_names = {
        str(variable_name)
        .strip()
        .lower()

        for variable_name
        in variable_values.keys()

        if str(variable_name).strip()
    }

    # When using the report-level `all` index, once the best
    # compatible FAQ variable combination is selected, keep all
    # few-shot examples on that same combination.
    #
    # This prevents mixing examples such as:
    #   no_variables
    #   oa_number
    #   customer_name,oa_number
    # in the same generation prompt.
    fallback_selected_variables_key = None

    if not isinstance(
        raw_results,
        list,
    ):
        rejected_results.append(
            {
                "reason": (
                    "invalid_search_results_type"
                ),
                "results_type": type(
                    raw_results
                ).__name__,
            }
        )

        return (
            prepared_results,
            rejected_results,
        )

    ranked_search_results = sorted(
        raw_results,
        key=lambda result: float(
            result.get(
                "similarity_score",
                0,
            )
            or 0
        )
        if isinstance(result, dict)
        else 0.0,
        reverse=True,
    )

    for search_match in ranked_search_results:

        if (
            len(prepared_results)
            >= top_k
        ):
            break

        if not isinstance(
            search_match,
            dict,
        ):
            rejected_results.append(
                {
                    "reason": (
                        "invalid_search_result"
                    ),
                    "result_type": type(
                        search_match
                    ).__name__,
                }
            )
            continue

        result_variables_key = (
            normalize_variables_key(
                str(
                    search_match.get(
                        "variables_key",
                        "",
                    )
                ).split(",")
            )
        )

        result_report_name = str(
            search_match.get(
                "report_name",
                "",
            )
        ).strip()

        # ========================================================
        # NORMAL EXACT-INDEX MODE
        # ========================================================

        if not fallback_used:

            searched_variables_key = (
                normalize_variables_key(
                    str(
                        search_match.get(
                            "searched_variables_key",
                            "",
                        )
                    ).split(",")
                )
            )

            is_valid = (
                result_variables_key
                == expected_variables_key

                and searched_variables_key
                == expected_variables_key

                and result_report_name.lower()
                == report_name.strip().lower()
            )

        # ========================================================
        # REPORT-LEVEL `all` INDEX FALLBACK MODE
        # ========================================================

        else:

            searched_variables_key = "all"

            result_variable_names = (
                _variables_key_to_set(
                    result_variables_key
                )
            )

            same_report = (
                result_report_name.lower()
                == report_name.strip().lower()
            )

            # Candidate is usable only when every variable required
            # by that FAQ is available in the incoming request.
            #
            # Examples:
            #
            # request: org_name
            # FAQ: no_variables
            # -> valid
            #
            # request: business_group,oa_number
            # FAQ: oa_number
            # -> valid
            #
            # request: business_group,oa_number
            # FAQ: customer_name,oa_number
            # -> invalid
            variables_are_available = (
                result_variable_names
                .issubset(
                    supplied_variable_names
                )
            )

            is_valid = (
                same_report
                and variables_are_available
            )

            # Once the highest-ranked compatible variable
            # combination is selected, only use examples from
            # that same combination.
            if is_valid:

                if (
                    fallback_selected_variables_key
                    is None
                ):
                    fallback_selected_variables_key = (
                        result_variables_key
                    )

                elif (
                    result_variables_key
                    != fallback_selected_variables_key
                ):
                    is_valid = False

        if not is_valid:
            rejected_results.append(
                {
                    "reason": (
                        "fallback_candidate_mismatch"
                        if fallback_used
                        else "index_metadata_mismatch"
                    ),
                    "result_variables_key": (
                        result_variables_key
                    ),
                    "searched_variables_key": (
                        searched_variables_key
                    ),
                    "expected_variables_key": (
                        expected_variables_key
                    ),
                    "result_report_name": (
                        result_report_name
                    ),
                    "expected_report_name": (
                        report_name
                    ),
                    "fallback_used": (
                        fallback_used
                    ),
                    "supplied_variable_names": (
                        sorted(
                            supplied_variable_names
                        )
                    ),
                    "fallback_selected_variables_key": (
                        fallback_selected_variables_key
                    ),
                    "excel_row_number": (
                        search_match.get(
                            "excel_row_number"
                        )
                    ),
                }
            )
            continue

        original_display_question = str(
            search_match.get(
                "display_question",
                "",
            )
        ).strip()

        original_sql = str(
            search_match.get(
                "sql",
                "",
            )
        ).strip()

        if not original_display_question:
            rejected_results.append(
                {
                    "reason": (
                        "missing_display_question"
                    ),
                    "excel_row_number": (
                        search_match.get(
                            "excel_row_number"
                        )
                    ),
                }
            )
            continue

        if not original_sql:
            rejected_results.append(
                {
                    "reason": "missing_sql",
                    "excel_row_number": (
                        search_match.get(
                            "excel_row_number"
                        )
                    ),
                }
            )
            continue

        try:
            injected_display_question = (
                inject_variable_values(
                    template=(
                        original_display_question
                    ),
                    variable_values=(
                        variable_values
                    ),
                    is_sql=False,
                )
            )

            injected_sql = (
                inject_variable_values(
                    template=original_sql,
                    variable_values=(
                        variable_values
                    ),
                    is_sql=True,
                )
            )

        except Exception as error:
            rejected_results.append(
                {
                    "reason": (
                        "example_injection_failed"
                    ),
                    "message": str(error),
                    "exception_type": type(
                        error
                    ).__name__,
                    "excel_row_number": (
                        search_match.get(
                            "excel_row_number"
                        )
                    ),
                }
            )
            continue

        prepared_results.append(
            {
                "option_id": (
                    f"option_"
                    f"{len(prepared_results) + 1}"
                ),
                "display_question": (
                    injected_display_question
                ),
                "matched_question": (
                    search_match.get(
                        "matched_question",
                        "",
                    )
                ),
                "sql": injected_sql,
                "display_question_template": (
                    original_display_question
                ),
                "sql_template": (
                    original_sql
                ),
                "variables_key": (
                    result_variables_key
                ),
                "searched_variables_key": (
                    searched_variables_key
                ),
                "report_name": (
                    result_report_name
                ),
                "distance": (
                    search_match.get(
                        "distance"
                    )
                ),
                "similarity_score": (
                    search_match.get(
                        "similarity_score"
                    )
                ),
                "similarity_percent": (
                    search_match.get(
                        "similarity_percent"
                    )
                ),
                "excel_row_number": (
                    search_match.get(
                        "excel_row_number"
                    )
                ),
            }
        )

    return (
        prepared_results,
        rejected_results,
    )