# Mist AP Rename

Bulk-rename Juniper Mist access points from a CSV file, using the [`mistapi`](https://pypi.org/project/mistapi/) Python library.

You give the script a CSV of **new name + MAC address + serial number**. For each row it finds the AP in your Mist org by MAC address and renames it. By default it only **previews** the changes; nothing is changed in Mist until you add `--apply` and confirm.

---

## Requirements

- Python 3.9 or newer
- A Mist API token with write access to the org (Super User or Network Admin)
- Your Mist cloud's API host (see the table below)

## Installation

```bash
git clone https://github.com/p9221066/MyMistAPRenameApp.git
cd MyMistAPRenameApp

# optional: use a virtual environment
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux

pip install -r requirements.txt
```

## Configuration

1. Copy the example settings file:

   ```bash
   copy .env.example .env     # Windows
   # cp .env.example .env     # macOS / Linux
   ```

2. Edit `.env` and fill in your values:

   ```ini
   MIST_HOST=api.mist.com
   MIST_APITOKEN=your_api_token_here
   ```

   | Mist cloud | `MIST_HOST` |
   |---|---|
   | Global 01 | `api.mist.com` |
   | Global 02 | `api.gc1.mist.com` |
   | Global 03 | `api.ac2.mist.com` |
   | Global 04 | `api.gc2.mist.com` |
   | Global 05 | `api.gc4.mist.com` |
   | EMEA 01 | `api.eu.mist.com` |
   | EMEA 02 | `api.gc3.mist.com` |
   | EMEA 03 | `api.ac6.mist.com` |
   | EMEA 04 | `api.gc6.mist.com` |
   | APAC 01 | `api.ac5.mist.com` |
   | APAC 02 | `api.gc5.mist.com` |
   | APAC 03 | `api.gc7.mist.com` |

   To find your cloud, look at the URL you use for the Mist portal: `manage.mist.com` → `api.mist.com`, `manage.eu.mist.com` → `api.eu.mist.com`, and so on.

3. **To create an API token:** in the Mist portal go to **Organization → Settings → API Token → Create Token**, or for a personal token go to **My Account → API Token**.

> `.env` is listed in `.gitignore`, so it is never committed. Don't share your token.

## Preparing the CSV

The CSV needs these columns (column names are not case-sensitive, and the column order doesn't matter):

| Column | Required | Description |
|---|---|---|
| `Name` | Yes | The **new** name to give the AP (64 characters max) |
| `MAC Address` | Yes | The AP's MAC address, used to find it in Mist |
| `Serial Number` | No (recommended) | Checked against the serial number in Mist so that a mistyped MAC can't rename the wrong AP |

MAC addresses can be written in any common format: `5c:5b:35:00:00:01`, `5C-5B-35-00-00-01`, `5c5b.3500.0001` or `5c5b35000001`.

Example ([aps_example.csv](aps_example.csv)):

```csv
Name,MAC Address,Serial Number
LON-L1-AP01,5c:5b:35:00:00:01,A0123456789AB
LON-L1-AP02,5c-5b-35-00-00-02,A0123456789AC
LON-L2-AP01,5c5b35000003,
```

Excel works fine: save the sheet as **CSV UTF-8 (Comma delimited)**.

## Usage

### 1. Preview (dry run)

```bash
python mist_ap_rename.py aps.csv
```

Example output:

```
ROW  MAC                CURRENT NAME  NEW NAME     STATUS
2    5c:5b:35:00:00:01  old1          LON-L1-AP01  rename
3    5C-5B-35-00-00-02  old2          LON-L1-AP02  error: serial mismatch
4    5c5b35000003       LON-L2-AP01   LON-L2-AP01  unchanged
5    5c5b35000099                     LON-L2-AP04  error: MAC not found in Mist

Total: 4  rename: 1  unchanged: 1  errors: 2 (error rows are skipped)

Dry-run only. Re-run with --apply to make these changes.
```

### 2. Save the plan for review (optional)

```bash
python mist_ap_rename.py aps.csv --report plan.csv
```

`plan.csv` lists each row with its current name, new name, serial numbers, site and device IDs, and status.

### 3. Apply the changes

```bash
python mist_ap_rename.py aps.csv --apply
```

The script shows the plan, then asks you to type `yes` before it renames anything. Only rows with status `rename` are changed. Add `--report result.csv` to save what happened to each row.

### All options

| Option | Description |
|---|---|
| `csv_file` | Path to the input CSV (required) |
| `--apply` | Rename the APs. Without it, the script only previews. |
| `--yes` | Skip the `yes` confirmation prompt (for use in scripts) |
| `--report FILE` | Write the plan or results to a CSV file |
| `--org-id ID` | Mist org ID. Found automatically if your token can see only one org; otherwise the script lists the orgs it can see. |
| `--env-file FILE` | Read settings from a different file (default `.env`) |
| `--no-serial-check` | Don't compare the CSV serial number with the one in Mist |

## Row statuses

| Status | Meaning | What to do |
|---|---|---|
| `rename` | Will be renamed (dry run) | — |
| `renamed` | Renamed successfully (`--apply`) | — |
| `unchanged` | The AP already has this name | — |
| `error: invalid MAC` | The MAC address isn't 12 hex digits | Fix the CSV |
| `error: empty name` | The Name cell is blank | Fix the CSV |
| `error: name longer than 64` | The new name is too long | Shorten it |
| `error: MAC repeated in CSV` | The same MAC appears on more than one row | Remove the duplicate |
| `error: name repeated in CSV` | Two rows have the same new name | Make the names unique |
| `error: MAC not found in Mist` | No AP with this MAC in the org inventory | Check the MAC and the org |
| `error: serial mismatch` | The CSV serial number differs from Mist's | Check the row, or use `--no-serial-check` |
| `error: AP not assigned to a site` | The AP is in the inventory but not at a site; Mist can only rename an AP once it's at a site | Assign it to a site first |
| `failed: HTTP <code>` | Mist rejected the rename | See the log line; often the token lacks write permission |

Rows with an error are skipped and the rest are still processed. The script exits with code `1` if any row had an error or failed, and `0` otherwise.

## How it works

1. Reads and checks the CSV.
2. Logs in to Mist with the API token from `.env`.
3. Downloads the org's full AP inventory once (`GET /api/v1/orgs/{org_id}/inventory?type=ap`) and matches each row by MAC address.
4. For each AP to rename, sends `PUT /api/v1/sites/{site_id}/devices/{device_id}` with `{"name": "<new name>"}`.

## Troubleshooting

- **`401` / `403` errors**: the token is invalid, has expired, or doesn't have write access to the org.
- **"Token has access to N orgs; pass --org-id"**: copy the org ID from the list the script prints and add `--org-id <id>`.
- **"missing column(s)"**: check the CSV header row spelling (`Name`, `MAC Address`).
- **Every row shows "MAC not found"**: check that `MIST_HOST` points to the right cloud and that you're using the right org.
