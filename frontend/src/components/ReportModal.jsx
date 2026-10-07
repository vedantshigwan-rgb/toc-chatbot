import { useMemo } from "react";
import { useChat } from "../context/ChatContext";


function formatColumnName(column) {
  if (!column) {
    return "";
  }

  return String(column)
    .replace(/_/g, " ")
    .toLowerCase()
    .replace(/\b\w/g, (char) =>
      char.toUpperCase()
    );
}


function isNumericValue(value) {
  if (
    value === null ||
    value === undefined ||
    value === ""
  ) {
    return false;
  }

  if (
    typeof value === "number"
  ) {
    return Number.isFinite(value);
  }

  if (
    typeof value !== "string"
  ) {
    return false;
  }

  const cleaned =
    value.trim();

  if (!cleaned) {
    return false;
  }

  return (
    /^-?\d+(\.\d+)?$/.test(
      cleaned
    )
  );
}


function formatNumber(value) {
  const number =
    Number(value);

  if (
    !Number.isFinite(number)
  ) {
    return value;
  }

  /*
   * Avoid ugly values such as:
   * 14777977.30595
   *
   * while preserving useful decimals.
   */
  const hasDecimals =
    Math.abs(
      number -
      Math.trunc(number)
    ) > 0.000001;

  return new Intl.NumberFormat(
    "en-IN",
    {
      maximumFractionDigits:
        hasDecimals ? 2 : 0,

      minimumFractionDigits: 0,
    }
  ).format(number);
}


function formatCellValue(value) {
  if (
    value === null ||
    value === undefined ||
    value === ""
  ) {
    return "—";
  }

  if (
    isNumericValue(value)
  ) {
    return formatNumber(value);
  }

  return String(value);
}


export default function ReportModal() {
  const {
    reportModal,
    closeReport,
  } = useChat();

  const {
    open,
    data,
  } = reportModal;


  const rowCount =
    Array.isArray(data?.rows)
      ? data.rows.length
      : 0;


  const numericColumns =
    useMemo(() => {
      if (
        !data?.rows ||
        !data?.columns
      ) {
        return new Set();
      }

      const detected =
        new Set();

      data.columns.forEach(
        (_, columnIndex) => {
          const sampleValues =
            data.rows
              .slice(0, 20)
              .map(
                (row) =>
                  row[columnIndex]
              )
              .filter(
                (value) =>
                  value !== null &&
                  value !== undefined &&
                  value !== ""
              );

          if (
            sampleValues.length > 0 &&
            sampleValues.every(
              isNumericValue
            )
          ) {
            detected.add(
              columnIndex
            );
          }
        }
      );

      return detected;
    }, [data]);


  function onOverlayClick(event) {
    if (
      event.target ===
      event.currentTarget
    ) {
      closeReport();
    }
  }


  function downloadCsv() {
    if (
      !data?.columns ||
      !data?.rows
    ) {
      return;
    }

    const escapeCsvValue = (
      value
    ) => {
      if (
        value === null ||
        value === undefined
      ) {
        return "";
      }

      const text =
        String(value);

      return (
        `"${text.replace(
          /"/g,
          '""'
        )}"`
      );
    };


    const csvRows = [
      data.columns.map(
        escapeCsvValue
      ),

      ...data.rows.map(
        (row) =>
          row.map(
            escapeCsvValue
          )
      ),
    ];


    const csv =
      csvRows
        .map(
          (row) =>
            row.join(",")
        )
        .join("\n");


    const blob =
      new Blob(
        [csv],
        {
          type:
            "text/csv;charset=utf-8;",
        }
      );


    const url =
      URL.createObjectURL(
        blob
      );


    const link =
      document.createElement(
        "a"
      );

    link.href = url;

    link.download =
      `${
        data.title ||
        "query-results"
      }.csv`
        .replace(
          /[^a-z0-9-_]+/gi,
          "_"
        );


    document.body.appendChild(
      link
    );

    link.click();

    document.body.removeChild(
      link
    );

    URL.revokeObjectURL(
      url
    );
  }


  return (
    <div
      className={
        "modal-overlay" +
        (open ? " show" : "")
      }
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
                {rowCount.toLocaleString(
                  "en-IN"
                )}{" "}
                {rowCount === 1
                  ? "record"
                  : "records"}
              </div>

            </div>


            <button
              className="modal-close"
              onClick={closeReport}
              aria-label="Close"
              title="Close"
            >
              ✕
            </button>

          </div>


          <div className="modal-body">

            <div className="table-scroll-container">

              <table className="report-table">

                <thead>
                  <tr>
                    {data.columns.map(
                      (
                        column,
                        columnIndex
                      ) => (
                        <th
                          key={column}
                          className={
                            numericColumns.has(
                              columnIndex
                            )
                              ? "numeric-column"
                              : ""
                          }
                        >
                          {formatColumnName(
                            column
                          )}
                        </th>
                      )
                    )}
                  </tr>
                </thead>


                <tbody>
                  {data.rows.map(
                    (
                      row,
                      rowIndex
                    ) => (
                      <tr key={rowIndex}>

                        {row.map(
                          (
                            cell,
                            columnIndex
                          ) => (
                            <td
                              key={
                                columnIndex
                              }
                              className={
                                numericColumns.has(
                                  columnIndex
                                )
                                  ? "numeric-column"
                                  : ""
                              }
                              title={
                                cell === null ||
                                cell === undefined
                                  ? ""
                                  : String(
                                      cell
                                    )
                              }
                            >
                              {formatCellValue(
                                cell
                              )}
                            </td>
                          )
                        )}

                      </tr>
                    )
                  )}
                </tbody>

              </table>

            </div>

          </div>


          <div className="modal-footer">

            <div className="modal-footer-info">
              Showing{" "}
              <strong>
                {rowCount.toLocaleString(
                  "en-IN"
                )}
              </strong>{" "}
              {rowCount === 1
                ? "record"
                : "records"}
            </div>


            <div className="modal-footer-actions">

              <button
                className="modal-btn"
                onClick={closeReport}
              >
                Close
              </button>

              <button
                className="modal-btn primary"
                onClick={downloadCsv}
              >
                ↓ Download CSV
              </button>

            </div>

          </div>

        </div>
      )}
    </div>
  );
}