import {
  useEffect,
  useRef,
  useState,
} from "react";


export default function Dropdown({
  label,
  options = [],
  selectedId = null,
  placeholder = "Select an option",
  disabled = false,
  allowNone = false,
  noneLabel = "None / No filter",
  onSelect,
}) {
  const [
    open,
    setOpen,
  ] = useState(false);


  const dropdownRef =
    useRef(null);


  const selectedOption =
    options.find(
      (option) =>
        option.id === selectedId
    ) || null;


  function optionLabel(option) {
    return (
      option?.label ||
      option?.name ||
      option?.id ||
      ""
    );
  }


  useEffect(() => {
    function handleOutsideClick(
      event
    ) {
      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(
          event.target
        )
      ) {
        setOpen(false);
      }
    }


    document.addEventListener(
      "mousedown",
      handleOutsideClick
    );


    return () => {
      document.removeEventListener(
        "mousedown",
        handleOutsideClick
      );
    };
  }, []);


  useEffect(() => {
    setOpen(false);
  }, [
    selectedId,
    options,
  ]);


  function toggleDropdown() {
    if (
      disabled ||
      (
        options.length === 0 &&
        !allowNone
      )
    ) {
      return;
    }

    setOpen(
      (currentValue) =>
        !currentValue
    );
  }


  return (
    <div className="sidebar-dropdown-block">
      <div className="section-label">
        {label}
      </div>


      <div
        className="dropdown"
        ref={dropdownRef}
      >
        <button
          type="button"

          className={
            "report-select" +
            (
              !selectedOption
                ? " placeholder"
                : ""
            )
          }

          disabled={
            disabled ||
            (
              options.length === 0 &&
              !allowNone
            )
          }

          onClick={
            toggleDropdown
          }
        >
          <span>
            {selectedOption
              ? optionLabel(
                  selectedOption
                )
              : allowNone
                ? noneLabel
                : placeholder}
          </span>

          <span>▾</span>
        </button>


        {open && (
          <div className="dropdown-menu">
            {allowNone && (
              <button
                type="button"

                className={
                  "dropdown-option" +
                  (
                    selectedId == null
                      ? " selected"
                      : ""
                  )
                }

                onClick={() => {
                  onSelect(null);
                  setOpen(false);
                }}
              >
                <span className="dropdown-option-content">
                  <span className="dropdown-option-label">
                    {noneLabel}
                  </span>
                </span>
              </button>
            )}

            {options.map(
              (option) => (
                <button
                  key={option.id}

                  type="button"

                  className={
                    "dropdown-option" +
                    (
                      option.id ===
                      selectedId
                        ? " selected"
                        : ""
                    )
                  }

                  onClick={() => {
                    onSelect(
                      option.id
                    );

                    setOpen(false);
                  }}
                >
                  <span className="dropdown-option-content">
                    <span className="dropdown-option-label">
                      {optionLabel(
                        option
                      )}
                    </span>

                    {option.secondary_label && (
                      <span className="dropdown-option-secondary">
                        {
                          option.secondary_label
                        }
                      </span>
                    )}
                  </span>
                </button>
              )
            )}
          </div>
        )}
      </div>
    </div>
  );
}