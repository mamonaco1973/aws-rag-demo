# --------------------------------------------------------------------------------
# DATA: archive_file.lambdas_zip
# --------------------------------------------------------------------------------
# Description:
#   Packages Lambda source code from the local "code" directory
#   into a ZIP archive for deployment.
#
# Expected code layout:
#   code/
#     get.py
#     list.py
#     create.py
#     update.py
#     delete.py
# --------------------------------------------------------------------------------
data "archive_file" "lambdas_zip" {
  type        = "zip"
  source_dir  = "${path.module}/code"
  output_path = "${path.module}/lambdas.zip"
}