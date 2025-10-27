# Gemini's Notes for the Qupath_to_LMD Project

This document is a summary for the Gemini assistant to quickly get up to speed when working on this project.

## Project Overview

The goal of this project is to create a Streamlit web application that converts GeoJSON polygon data from QuPath into an XML format suitable for a Laser Microdissection (LMD) machine. The app also provides UI tools to help with plate layout, sample mapping, and quality control.

## Key Files

-   `streamlit_app.py`: The main entry point and UI for the web application.
-   `src/qupath_to_lmd/utils.py`: Contains utility functions for plate generation, sample mapping, and dataframe styling.
-   `src/qupath_to_lmd/geojson_utils.py`: Handles the core logic of processing the GeoJSON files, coordinate transformations, and XML generation.
-   `src/qupath_to_lmd/st_cached.py`: Contains Streamlit cached functions for performance.

## User's Preferred Workflow

**IMPORTANT:** The user prefers to make code changes themselves. My role is to act as a consultant:
-   **Analyze** the code and the user's request.
-   **Recommend** a course of action, explaining the "why" behind the suggestion.
-   **Provide pseudo-code or structural examples** rather than complete, copy-pasteable code blocks.
-   **Wait for the user to implement the changes** before proceeding.

## Session Summary (as of 2025-10-22)

-   **Goal:** Improve the UX of the Streamlit app in `Step 3`.
-   **Initial Problem:** The app was not interactive. Users had to re-click buttons to see updates after changing input widgets. Dataframes were also not full-width.
-   **Changes Made (by user, based on my recommendation):**
    1.  **Refactored Step 3 to use `st.session_state`:** A `view_mode` variable in the session state now tracks whether to display the 'default' or 'samples' dataframe.
    2.  **Made UI Dynamic:** Clicking a button now sets the `view_mode`. Any change to input widgets (e.g., plate margins, randomize toggle) triggers a script rerun, and the displayed dataframe automatically updates based on the stored `view_mode`.
    3.  **Fixed Layout:** The `st.dataframe` calls were moved outside of the `st.columns` blocks to allow them to render at full width (`use_container_width=True`).
    4.  **Bug Fix:** Corrected the 'default' view to use the user-selected `plate_type` instead of always defaulting to 384 wells.

## Next Steps Hint

The user has opened `src/qupath_to_lmd/geojson_utils.py`, suggesting that future work might focus on the backend logic for GeoJSON processing.
