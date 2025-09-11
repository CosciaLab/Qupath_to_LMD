import streamlit as st
import sys
import pandas

def patch_streamlit():
    """
    Monkey-patches the Streamlit library with dummy functions to allow
    functions with st.* calls to be used outside of a Streamlit app
    (e.g., in a Jupyter notebook).
    """

    def dummy_write(*args, **kwargs):
        """Replaces st.write to just print to the console."""
        print(*args)

    def dummy_success(*args, **kwargs):
        """Replaces st.success with a print statement."""
        print(f"✅ Success: {' '.join(map(str, args))}")

    def dummy_warning(*args, **kwargs):
        """Replaces st.warning with a print statement."""
        print(f"⚠️ Warning: {' '.join(map(str, args))}")
        
    def dummy_error(*args, **kwargs):
        """Replaces st.error with a print statement."""
        print(f"❌ Error: {' '.join(map(str, args))}")

    def dummy_stop():
        """Replaces st.stop to raise a SystemExit."""
        print("--- Streamlit stop() called ---")
        raise SystemExit()

    def dummy_table(data):
        """Replaces st.table to print a pandas DataFrame."""
        if isinstance(data, pandas.DataFrame):
            print(data.to_string())
        else:
            print(data)

    def do_nothing(*args, **kwargs):
        """A function that does nothing, for replacing UI elements."""
        pass

    # Apply the monkey-patch
    st.write = dummy_write
    st.success = dummy_success
    st.warning = dummy_warning
    st.error = dummy_error
    st.stop = dummy_stop
    st.table = dummy_table
    st.image = do_nothing
    
    # Replace the decorator with a pass-through function
    st.cache_data = lambda func: func

    print("Streamlit has been patched for non-interactive use.")
