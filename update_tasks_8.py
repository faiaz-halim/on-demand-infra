import json
from datetime import datetime, timezone

tasks_file_path = 'tasks.json'
# Use a fixed timestamp for reproducibility if this script were run multiple times for the same conceptual "current time"
# For a real CI/automation, datetime.now(timezone.utc) is fine.
# Let's use a timestamp slightly after the last one in tasks.json (which was 2024-06-01T15:15:00Z for meta.updatedAt)
current_time_iso = "2024-06-01T15:20:00Z" # Manually set for this update turn

try:
    with open(tasks_file_path, 'r') as f:
        data = json.load(f)
except FileNotFoundError:
    print(f"Error: {tasks_file_path} not found.")
    exit(1)
except json.JSONDecodeError:
    print(f"Error: Could not decode JSON from {tasks_file_path}.")
    exit(1)


data['meta']['updatedAt'] = current_time_iso

task_found = False
for task in data.get('tasks', []):
    if task.get('id') == 8:
        task_found = True
        task['status'] = 'done'
        task['updatedAt'] = current_time_iso
        # Update descriptions for subtasks if they are minimal placeholders
        subtask_details = {
            "8.1": "Allow updating the application in the Kind cluster on EC2 with a new image or configuration.",
            "8.2": "Allow scaling the application deployment within the Kind cluster on EC2.",
            "8.3": "Provide functionality to completely remove the 'cloud-local' environment by destroying the Terraform-managed EC2 instance and related AWS resources."
        }
        if 'subtasks' in task:
            for subtask in task['subtasks']:
                if subtask.get('id') in ["8.1", "8.2", "8.3"]:
                    subtask['status'] = 'done'
                    subtask['updatedAt'] = current_time_iso
                    # Update description if it's just "pending" or a short placeholder
                    if subtask.get('description') == 'pending' or len(subtask.get('description', '')) < 20 :
                         if subtask.get('id') in subtask_details:
                            subtask['description'] = subtask_details[subtask.get('id')]
        break

if not task_found:
    print(f"Error: Task with id 8 not found in {tasks_file_path}.")
    # Optionally, create it if it's missing, but for now, assume it exists.
    # exit(1) # Or handle by appending if necessary

try:
    with open(tasks_file_path, 'w') as f:
        json.dump(data, f, indent=2)
    print(f"Successfully updated {tasks_file_path} for Task 8.")
except Exception as e:
    print(f"Error writing {tasks_file_path}: {e}")
    exit(1)
