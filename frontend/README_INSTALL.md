# TOC Mode Upgrade — installation and test instructions

Baseline: `TOC_backend_primary_only_7.zip` + `Frontend_2.zip` + `redis_1.zip`.

The package is a **changed-files-only overlay**. It is **not** a complete runnable backend/frontend. The directories `backend/` and `frontend/` are paths relative to the root of your current TOC project. The original code and the Redis service are not replaced.

## What changed

### Frontend

| Path | Modification |
|---|---|
| `frontend/src/components/Sidebar/Sidebar.jsx` | Only Ask displays the report filters and variable list. Report selector remains visible in all modes. |
| `frontend/src/components/MainPanel/InputArea.jsx` | Rename `Metadata` to `Report Terms`, improve placeholders/help text, hide slash picker outside Ask. API mode remains `metadata`. |
| `frontend/src/components/MainPanel/LookupPopover.jsx` | Additional safety guard against dropdown opening in Help or Report Terms. |
| `frontend/src/context/ChatContext.jsx` | Stop slash autocomplete and send no sidebar/inline variables for Help or Report Terms. Do not alter Ask/feedback variable logic. |
| `frontend/src/components/HelpPanel.jsx` | Update on-screen instructions; remove developer-facing terminology. |
| `frontend/src/utils/responseActions.js` | Rename a public action label to `Report term not found`. |

### Backend

| Path | Modification |
|---|---|
| `backend/data/help/help_knowledge.json` | Rewrite general user-facing application Help knowledge. |
| `backend/data/help/report_variable_descriptions.json` | **New:** 163 approved description entries extracted from 10 tables of `FAQ_Variable_Descriptions_UPDATED.docx`. The two duplicate report/variable entries remain separate. |
| `backend/services/report_variable_help_service.py` | **New:** exact variable-name lookup scoped to the selected report. |
| `backend/services/help_service.py` | Use exact descriptions first, then scoped Help FAISS. Ground the explanation in the approved text. For supporting models, optional `HELP_RESPONSE_TEMPERATURE` (default `0.55`). |
| `backend/services/help_retrieval_service.py` | Prevent report-variable documents from a different report reaching Help FAISS answers. |
| `backend/scripts/build_help_faiss.py` | Include new report-variable descriptions in Help FAISS. **Must run once after installing.** |
| `backend/services/metadata_safe_context.py` | **New:** strict metadata-field allowlists. Known distinct values/role codes in descriptions are redacted. |
| `backend/services/metadata_query_service.py` | Reject requests for actual stored values / role access; use safe candidate context; return only safe match labels, not raw metadata. |
| `backend/services/metadata_response_service.py` | Send only approved descriptions/units/field names to the answer LLM; business-term explanation prompt. |
| `backend/workflows/workflow_manager.py` | Keep full Help matches and metadata candidates out of public diagnostics. |
| `backend/main.py` | Ignore all input/sidebar variables for explicit Help and Report Terms requests (even when called directly through Postman). |
| `backend/services/result_analysis_service.py` | Adaptive level of explanation for immediate SQL results and conversational follow-ups; no change to calculations/SQL generation. |
| `backend/tests/test_report_modes_upgrade.py` | **New:** offline safety and report-scoping tests. |

### Redis

**No changes** to the Redis API or its JSON data. Its autocomplete continues serving Ask mode.

## Windows CMD installation (safe overlay)

1. **Stop** your running backend and frontend servers.
2. Make a backup, or commit your existing repository before copying. For example, in the **current repository root**:

   ```bat
   cd /d "D:\AI gen\TOC-chatbot"
   git status
   git add .
   git commit -m "Checkpoint before Help and Report Terms upgrade"
   ```

   Only commit if `git status` shows changes you intend to keep. If you have untracked sensitive config or local environments, do **not** stage those; back up your project instead.

3. Extract `TOC_Mode_Upgrade.zip` to a convenient folder, such as `D:\TOC_Mode_Upgrade`. After extraction it must contain `backend`, `frontend`, and this README.
4. In **CMD**, overlay only the changed files onto the correct repository paths:

   ```bat
   robocopy "D:\TOC_Mode_Upgrade\backend" "D:\AI gen\TOC-chatbot\backend" /E /R:1 /W:1
   robocopy "D:\TOC_Mode_Upgrade\frontend" "D:\AI gen\TOC-chatbot\frontend" /E /R:1 /W:1
   ```

   `robocopy` returns code `1` after successfully copying files. That is normal; the source directories contain only changed files. **Do not use `/MIR`**.

5. Activate the **existing working** backend virtual environment, from the repository's backend directory, and verify its dependencies:

   ```bat
   cd /d "D:\AI gen\TOC-chatbot\backend"
   .venv\Scripts\activate
   python --version
   python -c "import faiss, numpy, openai; print('Required modules available')"
   ```

   If your venv resides somewhere else, activate that existing environment. Do **not** create a second virtual environment unless needed.

6. **Rebuild the Help index once**, using the same environment configured with an OpenAI API key:

   ```bat
   python -m scripts.build_help_faiss
   ```

   This creates/updates `backend\data\help_faiss\index.faiss`, `documents.json`, and `config.json`. It makes an embeddings API call, which incurs API usage. **Do not copy the old Help FAISS index over the rebuilt one.**

   If your project reads the API key from a `.env` file, start the command the same way you normally start your backend so those environment variables are available.

7. Run the new offline tests:

   ```bat
   python -m unittest discover -s tests -p test_report_modes_upgrade.py -v
   ```

8. Verify the frontend:

   ```bat
   cd /d "D:\AI gen\TOC-chatbot\frontend"
   npm ci
   npm run build
   npm run verify
   ```

9. Restart backend, Redis API (unchanged), and frontend using your **existing working start commands**.

## Configuration / LLM settings

- `HELP_MODEL`: default `gpt-4.1-mini` (unchanged).
- `HELP_RESPONSE_TEMPERATURE`: default `0.55`; only sent for `gpt-4.1*` and `gpt-4o*` models. If you change Help to a different family, the code omits `temperature` for compatibility.
- `METADATA_RESPONSE_MODEL`: default `gpt-5-mini` (unchanged).
- `METADATA_QUERY_MODEL`: default `gpt-5-mini` (unchanged). Do **not** increase candidate-resolution randomness.
- `HELP_TOP_K` and `HELP_MIN_SCORE`: retain their existing behavior.

Do not add `temperature` to `gpt-5-mini` calls; those models may reject custom temperature. Richer Report Terms / SQL answers are obtained through prompting rather than a temperature change.

## Postman smoke tests

Use your existing authenticated `POST /ask` request, with the correct Authorization header. Replace report names with the exact values of your dropdown:

**Help — report-specific variable**

```json
{"message_type":"help","input":"What does days mean?","report_name":"Inventory Report","variables":[]}
```

Then use the same request with `"report_name":"BOM Report"`. The meanings must differ.

**Report Terms — business definition**

```json
{"message_type":"metadata","input":"What does ORG mean in this report?","report_name":"Inventory Report","variables":[]}
```

Verify the answer and the **entire API response** do not contain distinct values, role identifiers, raw tables or raw candidate records.

**Report Terms — blocked data request**

```json
{"message_type":"metadata","input":"List all distinct values of ORG_NAME","report_name":"Inventory Report","variables":[]}
```

It should tell the user to use Ask, not show stored values.

**Help — defensive input**

```json
{"message_type":"help","input":"What does buyer mean?","report_name":"Inventory Report","variables":[{"name":"buyer","value":"SOME_BUYER","source":"input"}]}
```

The backend must ignore the supplied variables; the persisted and returned request variables should be empty. It should give a definition, not retrieve buyer data.

**Frontend manual checks**

- `Ask`: report filters, Variables for this report, `/` suggestions all available.
- `Help`: only Report selector in sidebar; no filters/variable chips; typing `/` creates no suggestion popup.
- `Report Terms`: same minimal sidebar and no `/` suggestions.
- Return to `Ask`: previous Ask functionality remains intact.
- Switch modes while suggestions are loading; stale popups must not reappear.
- Reopen a report-locked chat; report selection, history and Ask data queries should still work.

## Important limitations / review before production

1. This package was tested offline with Python syntax checks and targeted mocked safety tests, **not** against your live OpenAI, Oracle, Redis, MongoDB, or Weaviate services.
2. The original `ColumnMetadata` descriptions are still stored as written. The safe context strips known `distinct_values` from descriptions, but values independently embedded in free-form descriptions and **absent from the distinct-values list** cannot be identified reliably. Audit the descriptions in Weaviate before production, and prefer clean business-only definitions.
3. The uploaded DOCX has 10 reports and 163 description rows, including two duplicate report-variable keys. Do not silently merge those descriptions. The metadata catalog has 12 reports; **POB COGS Report** and **POB COGS Spares Report** are missing from the DOCX and still need approved Help variable descriptions.
4. The FAISS index must be rebuilt or vague Help questions may use stale material. An old `index.faiss` will not match updated documents.
5. Frontend full build was not completed in the preparation environment because npm dependency installation failed; run `npm ci && npm run build && npm run verify` locally before deployment.
