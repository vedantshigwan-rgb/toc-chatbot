import { useChat } from "../context/ChatContext";

export default function HelpPanel() {
  const { helpOpen, toggleHelp } = useChat();

  return (
    <div className="help-wrap">
      <button className="help-btn" onClick={toggleHelp} aria-label="Help">
        i
      </button>
      <div className={"help-panel" + (helpOpen ? " show" : "")}>
        <div className="help-panel-title">
          Using the chatbot
          <button className="help-panel-close" onClick={toggleHelp} aria-label="Close">
            ✕
          </button>
        </div>
        1. Select a report for report-specific questions. Each chat stays linked to one report.
        <br />
        2. Use <strong>Ask</strong> to retrieve and analyze report data.
        <br />
        3. In Ask mode only, use sidebar filters or type <code>/</code> to insert a report variable.
        <br />
        4. Use <strong>Help</strong> to understand variables in the selected report or learn how to use the app.
        <br />
        5. Use <strong>Report Terms</strong> to understand a column or business term in your report.
        <br />
        6. Help and Report Terms need only report selection; no filters or slash variables.
        <br />
        7. Use the reply or modify action on a previous data result to refine that question.
      </div>
    </div>
  );
}
