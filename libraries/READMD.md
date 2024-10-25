Install all necessary packages like so:

```bash
pip install \
--platform manylinux2014_aarch64 \
--target=./libraries/packages \
--implementation cp \
--python-version 3.12 \
--only-binary=:all: --upgrade \
reportlab pillow
```

Then, go into the `packages` folder:
```bash
cd libraries/packages/
```

Zip the packages:
```bash
zip -qr ../packages.zip .
```

Go to the previous directory, and add `lambda_handler.py` into the zip file:
```bash
cd ..
zip packages.zip lambda_function.py
```

Finally, upload the entire `packages.zip` to the lambda function as a zip file. You may need to uplaod it to S3 first if the file is too big. 