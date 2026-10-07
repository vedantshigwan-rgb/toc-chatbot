import {
  useChat,
} from "../../context/ChatContext";


export default function TopBar() {
  const {
    selectedBaseTab,
    selectedSubTab,
    selectedReport,
    selectedOption,
  } = useChat();


  return (
    <div className="topbar">
      <div>
        <div className="topbar-report-name">
          {selectedReport
            ? selectedReport.label
            : "Select a report"}
        </div>

        {selectedReport && (
          <div className="topbar-report-path">
            {selectedBaseTab?.label}
            {" / "}
            {selectedSubTab?.label}
            {" / "}
            {selectedReport.label}

            {selectedOption && (
              <>
                {" / "}
                {selectedOption.label}
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}