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
        1. Use <strong>Ask</strong> for normal questions and data requests.
        <br />
        2. Select a report before using <strong>Help</strong> or <strong>Metadata</strong> mode.
        <br />
        3. Type <code>/</code> to insert a report variable, for example <code>{"/buyer_name {}"}</code>.
        <br />
        4. Enter or select the value inside the braces, for example <code>{"/buyer_name {ACME}"}</code>.
        <br />
        5. Sidebar options are optional filters; choosing None sends no option variable.
        <br />
        6. Use <strong>Modify</strong> only on a previous Primary result to send backend feedback reconstruction.
      </div>
    </div>
  );
}
