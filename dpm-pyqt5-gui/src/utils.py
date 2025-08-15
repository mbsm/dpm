def validate_process_name(name):
    if not name or len(name) < 3:
        raise ValueError("Process name must be at least 3 characters long.")
    return True

def format_command(command):
    return command.strip()

def is_valid_host(host):
    # Placeholder for host validation logic
    return True if host else False

def display_error_message(message):
    # Placeholder for displaying error messages in the GUI
    print(f"Error: {message}")  # Replace with actual GUI error display logic

def clear_input_fields(fields):
    for field in fields:
        field.clear()  # Assuming fields have a clear method, adjust as necessary