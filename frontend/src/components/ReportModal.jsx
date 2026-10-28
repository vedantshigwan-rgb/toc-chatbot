
import { useEffect, useMemo, useState } from "react";
import { useChat } from "../context/ChatContext";
import {
  fetchMessagePage,
  exportMessageCsv,
} from "../services/chatApi";


const PAGE_SIZE = 50;


function formatColumnName(column) {
  if (!column) return "";

  return String(column)
    .replace(/_/g, " ")
    .toLowerCase()
    .replace(/\b\w/g, (char) => char.toUpperCase());
}


function isNumericValue(value) {
  if (
    value === null ||
    value === undefined ||
    value === ""
  ) {
    return false;
  }

  if (typeof value === "number") {
    return Number.isFinite(value);
  }

  if (typeof value !== "string") {
    return false;
  }

  const cleaned = value.trim();

  return cleaned !== "" && /^-?\d+(\.\d+)?$/.test(cleaned);
}


function formatNumber(value) {
  const number = Number(value);

  if (!Number.isFinite(number)) {
    return value;
  }

  const hasDecimals =
    Math.abs(number - Math.trunc(number)) > 0.000001;

  return new Intl.NumberFormat("en-IN", {
    maximumFractionDigits: hasDecimals ? 2 : 0,
    minimumFractionDigits: 0,
  }).format(number);
}


function formatDateValue(value) {
  if (typeof value !== "string") {
    return value;
  }

  // Preserve dates that already use DD-MM-YY.
  if (/^\d{2}-\d{2}-\d{2}$/.test(value)) {
    return value;
  }

  // Convert ISO date/timestamp strings without timezone conversion.
  const match = value.match(
    /^(\d{4})-(\d{2})-(\d{2})(?:T.*)?$/
  );

  if (!match) {
    return value;
  }

  const [, year, month, day] = match;

  return `${day}-${month}-${year.slice(-2)}`;
}


function formatCellValue(value, column) {
  if (
    value === null ||
    value === undefined ||
    value === ""
  ) {
    return "—";
  }

  const columnName = String(column || "")
    .trim()
    .toUpperCase();

  // Keep OA_NUMBER exactly as supplied; never add commas.
  if (columnName === "OA_NUMBER", "TROLLEY_NO") {
    return String(value);
  }

  // Only format columns explicitly identified as date columns.
  const dateColumns = new Set([
    // Add the exact Oracle date column names here.
    "EXPECTED_DATE",
    "OA_PROMISE_DATE",
    "ADVANCE_HOLD_RELEASE_DATE",
    "BOM_RELEASE_DATE",
    "OA_NEED_BY_DATE",
    "OCD",
    "SCHEDULE_SHIP_DATE",
    "NEED_BY_DATE",
    "OA_CREATION_DATE",
    "FULL_KIT_MRP_SUGG_DATE",
    "OLD_PO_DATE",
    "EXPECTED_DATE",
    "BOM_EFF_DATE",
    "LOAD_DATE",
    'OA_CDD',
    "OA_FK_DATE",
    "OA_SSD",
    "PO_CREATION_DATE",
    "SUPPLIER_EXPECTED_DATE",
    "UTILIZATION_DATE",
    "CREATION_DATE",
    "PI_DATE",
    "CUST_PO_DATE",
    "INV_DATE",
    "LAST_UPDATE_DATE",
    "ORDER_DATE",
    "PLANNING_SALE_DATE",
    "DATE_ATTRIBUTE_2",
    "MARKETING_DATE",
    "QUALITY_DATE",
    "STORE_DATE",
    "ASSEMBLY_DATE",
    "SCM_DATE",
    "DESIGN_DATE",
    "TROLLEY_DATE",
  ]);

  if (dateColumns.has(columnName)) {
    return formatDateValue(String(value));
  }

  if (isNumericValue(value)) {
    return formatNumber(value);
  }

  return String(value);
}

/*
 * Supports backend results returned as either:
 *   [{ COLUMN_A: value, COLUMN_B: value }]
 * or:
 *   [[valueA, valueB]]
 */
function normalizePageResults(results, fallbackColumns = []) {
  if (!Array.isArray(results) || results.length === 0) {
    return {
      columns: fallbackColumns,
      rows: [],
    };
  }

  const firstRow = results[0];

  if (
    firstRow &&
    typeof firstRow === "object" &&
    !Array.isArray(firstRow)
  ) {
    const columns = Object.keys(firstRow);

    return {
      columns,
      rows: results.map((row) =>
        columns.map((column) => {
          const value = row?.[column];

          if (value === undefined) return null;

          if (typeof value === "object" && value !== null) {
            return JSON.stringify(value);
          }

          return value;
        })
      ),
    };
  }

  return {
    columns: fallbackColumns,
    rows: results.map((row) =>
      Array.isArray(row) ? row : []
    ),
  };
}


function getErrorMessage(error) {
  return (
    error?.message ||
    "Something went wrong while loading the report."
  );
}


function triggerBlobDownload(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");

  link.href = url;
  link.download = filename;

  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);

  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}


export default function ReportModal() {
  const {
    reportModal,
    closeReport,
  } = useChat();

  const { open, data } = reportModal;

  const messageId = data?.messageId || null;

  const [currentPage, setCurrentPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [totalCount, setTotalCount] = useState(null);
  const [pageData, setPageData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState("");

  /*
   * Reset pagination whenever a different report is opened.
   */
  useEffect(() => {
    if (!open) return;

    setCurrentPage(1);
    setTotalPages(1);
    setTotalCount(null);
    setPageData(null);
    setError("");
  }, [open, messageId]);

  /*
   * Fetch one server-side page for persisted assistant messages.
   * Reports without a message ID continue using their local rows.
   */
  useEffect(() => {
    if (!open || !messageId) return;

    let cancelled = false;

    async function loadPage() {
      setLoading(true);
      setError("");

      try {
        const response = await fetchMessagePage({
          messageId,
          page: currentPage,
        });

        if (cancelled) return;

        const results = Array.isArray(response?.results)
          ? response.results
          : [];

        const normalized = normalizePageResults(
          results,
          data?.columns || []
        );

        setPageData(normalized);

        setCurrentPage(
          Number(response?.page) || currentPage
        );

        setTotalPages(
          Math.max(1, Number(response?.total_pages) || 1)
        );

        setTotalCount(
          Number.isFinite(Number(response?.total_count))
            ? Number(response.total_count)
            : null
        );
      } catch (err) {
        if (!cancelled) {
          setError(getErrorMessage(err));
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    loadPage();

    return () => {
      cancelled = true;
    };
  }, [open, messageId, currentPage]);

  const columns = pageData?.columns ?? data?.columns ?? [];
  const rows = messageId
    ? (pageData?.rows ?? [])
    : (data?.rows ?? []);

  const rowCount = rows.length;

  const numericColumns = useMemo(() => {
    if (!rows.length || !columns.length) {
      return new Set();
    }

    const detected = new Set();

    columns.forEach((_, columnIndex) => {
      const sampleValues = rows
        .slice(0, 20)
        .map((row) => row[columnIndex])
        .filter(
          (value) =>
            value !== null &&
            value !== undefined &&
            value !== ""
        );

      if (
        sampleValues.length > 0 &&
        sampleValues.every(isNumericValue)
      ) {
        detected.add(columnIndex);
      }
    });

    return detected;
  }, [rows, columns]);

  function onOverlayClick(event) {
    if (event.target === event.currentTarget) {
      closeReport();
    }
  }

  async function downloadCsv() {
    if (!data) return;

    setError("");

    /*
     * Persisted report: export the complete SQL result from the backend.
     * This does not export only the currently visible page.
     */
    if (messageId) {
      setExporting(true);

      try {
        const blob = await exportMessageCsv({ messageId });

        const filename = `${
          data.title || "query-results"
        }.csv`.replace(/[^a-z0-9-_]+/gi, "_");

        triggerBlobDownload(blob, filename);
      } catch (err) {
        setError(getErrorMessage(err));
      } finally {
        setExporting(false);
      }

      return;
    }

    /*
     * Legacy/mock report: preserve the existing local CSV export.
     */
    if (!columns.length || !rows.length) return;

    const escapeCsvValue = (value) => {
      if (value === null || value === undefined) {
        return "";
      }

      return `"${String(value).replace(/"/g, '""')}"`;
    };

    const csvRows = [
      columns.map(escapeCsvValue),
      ...rows.map((row) => row.map(escapeCsvValue)),
    ];

    const csv = csvRows
      .map((row) => row.join(","))
      .join("\n");

    const blob = new Blob(
      ["\uFEFF", csv],
      { type: "text/csv;charset=utf-8;" }
    );

    const filename = `${
      data.title || "query-results"
    }.csv`.replace(/[^a-z0-9-_]+/gi, "_");

    triggerBlobDownload(blob, filename);
  }

  function goToPage(nextPage) {
    if (
      loading ||
      nextPage < 1 ||
      nextPage > totalPages ||
      nextPage === currentPage
    ) {
      return;
    }

    setCurrentPage(nextPage);
  }

  return (
    <div
      className={"modal-overlay" + (open ? " show" : "")}
      onClick={onOverlayClick}
    >
      {data && (
        <div className="modal-card">
          <div className="modal-header">
            <div className="modal-heading">
              <div className="modal-title">
                {data.title}
              </div>

              <div className="modal-subtitle">
                {messageId && totalCount !== null
                  ? `${totalCount.toLocaleString("en-IN")} total records`
                  : `${rowCount.toLocaleString("en-IN")} ${
                      rowCount === 1 ? "record" : "records"
                    }`}
              </div>
            </div>

            <button
              className="modal-close"
              onClick={closeReport}
              aria-label="Close"
              title="Close"
            >
              ×
            </button>
          </div>

          <div className="modal-body">
            {loading && (
              <div className="modal-subtitle">
                Loading page {currentPage}…
              </div>
            )}

            {error && (
              <div
                role="alert"
                style={{ padding: "10px", color: "#b91c1c" }}
              >
                {error}
              </div>
            )}

            <div className="table-scroll-container">
              <table className="report-table">
                <thead>
                  <tr>
                    {columns.map((column, columnIndex) => (
                      <th
                        key={`${column}-${columnIndex}`}
                        className={
                          numericColumns.has(columnIndex)
                            ? "numeric-column"
                            : ""
                        }
                      >
                        {formatColumnName(column)}
                      </th>
                    ))}
                  </tr>
                </thead>

                <tbody>
                  {!loading && rows.length === 0 && (
                    <tr>
                      <td
                        colSpan={Math.max(columns.length, 1)}
                        style={{ textAlign: "center", padding: "20px" }}
                      >
                        {error
                          ? "Could not load report records."
                          : "No records found."}
                      </td>
                    </tr>
                  )}

                  {rows.map((row, rowIndex) => (
                    <tr key={rowIndex}>
                      {row.map((cell, columnIndex) => (
                        <td
                          key={columnIndex}
                          className={
                            numericColumns.has(columnIndex)
                              ? "numeric-column"
                              : ""
                          }
                          title={
                            cell === null || cell === undefined
                              ? ""
                              : String(cell)
                          }
                        >
                          {formatCellValue(cell, columns[columnIndex])}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="modal-footer">
            <div className="modal-footer-info">
              {messageId ? (
                <>
                  Page{" "}
                  <strong>
                    {currentPage.toLocaleString("en-IN")}
                  </strong>{" "}
                  of{" "}
                  <strong>
                    {totalPages.toLocaleString("en-IN")}
                  </strong>
                  {" · "}
                  Showing{" "}
                  <strong>
                    {rowCount.toLocaleString("en-IN")}
                  </strong>{" "}
                  records
                </>
              ) : (
                <>
                  Showing{" "}
                  <strong>
                    {rowCount.toLocaleString("en-IN")}
                  </strong>{" "}
                  {rowCount === 1 ? "record" : "records"}
                </>
              )}
            </div>

            <div className="modal-footer-actions">
              {messageId && totalPages > 1 &&(
                <>
                  <button
                    className="modal-btn"
                    onClick={() => goToPage(currentPage - 1)}
                    disabled={loading || currentPage <= 1}
                  >
                    Previous
                  </button>

                  <button
                    className="modal-btn"
                    onClick={() => goToPage(currentPage + 1)}
                    disabled={loading || currentPage >= totalPages}
                  >
                    Next
                  </button>
                </>
              )}

              <button
                className="modal-btn"
                onClick={closeReport}
              >
                Close
              </button>

              <button
                className="modal-btn primary"
                onClick={downloadCsv}
                disabled={exporting || loading}
              >
                {exporting ? "Preparing CSV…" : "↓ Download CSV"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
