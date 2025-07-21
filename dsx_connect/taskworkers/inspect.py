import pyzipper
import zipfile
from io import BytesIO

def inspect_and_try_passwords(zip_bytes: bytes, candidates: list[bytes]) -> dict:
    """
    1) Peeks at each ZipInfo to report how it’s compressed/encrypted.
    2) Returns the first candidate password (as bytes) that successfully opens at least one entry.
       If none work, returns None.

    Args:
        zip_bytes: the raw .zip file as bytes
        candidates: a list of byte‐strings to try as passwords

    Returns:
        {
            "files": [
                {
                    "filename": "foo.txt",
                    "compress_type": "ZIP_DEFLATED",
                    "is_encrypted": True,
                    "aes_strength": 256,    # if AES
                },
                …
            ],
            "good_password": b"infected"  # or None if nothing worked
        }
    """

    bio = BytesIO(zip_bytes)

    # Use pyzipper.AESZipFile just to read the directory;
    # we do NOT pass a password yet.
    with pyzipper.AESZipFile(bio, mode="r") as zf:
        infos = zf.infolist()

        files_info = []
        for zi in infos:
            # 1) compression algorithm:
            comp = zi.compress_type
            if comp == zipfile.ZIP_DEFLATED:
                comp_name = "ZIP_DEFLATED"
            elif comp == zipfile.ZIP_LZMA:
                comp_name = "ZIP_LZMA"
            elif comp == zipfile.ZIP_BZIP2:
                comp_name = "ZIP_BZIP2"
            else:
                comp_name = f"OTHER({comp})"

            # 2) is the entry encrypted at all?
            #    Bit 0 of flag_bits is “encrypted” flag.
            is_encrypted = bool(zi.flag_bits & 0x0001)

            # 3) If AES, pyzipper stores extra fields under 'extra' or you can look for
            #    the 0x9901 (AES) extra-field header. We can parse that manually:
            aes_strength = None
            extra = zi.extra or b""
            i = 0
            while i + 4 <= len(extra):
                header_id = int.from_bytes(extra[i : i + 2], "little")
                data_size = int.from_bytes(extra[i + 2 : i + 4], "little")
                if header_id == 0x9901:
                    # AES extra-field: structure is
                    #   [2‐byte version, 2‐byte vendorID, 1‐byte AES_strength, 2‐byte actual_compress_type]
                    # so the 5th byte is AES strength (1=128, 2=192, 3=256)
                    aes_strength = extra[i + 4 + 2] * 64  # 1→64? Actually: 1→128, 2→192, 3→256
                    # Correct formula: 1→128, 2→192, 3→256
                    # So -> 1*64 + 64 = 128, etc. Or:
                    aes_strength = {1: 128, 2: 192, 3: 256}.get(extra[i + 4 + 2], None)
                    break
                i += 4 + data_size

            files_info.append({
                "filename": zi.filename,
                "compress_type": comp_name,
                "is_encrypted": is_encrypted,
                "aes_strength": aes_strength,
            })

        # 4) Now try candidate passwords (only on the first encrypted entry we find)
        good_password: bytes | None = None
        for zi in infos:
            if zi.flag_bits & 0x0001:  # only try a password if this entry is marked encrypted
                # try each candidate against this one entry
                for pwd in candidates:
                    try:
                        # attempt to open/read just 1 KB (or less) to test decryption
                        with pyzipper.AESZipFile(bio, mode="r",
                                                 compression=zi.compress_type,
                                                 encryption=pyzipper.WZ_AES) as trial_zf:
                            trial_zf.setpassword(pwd)
                            # zipped.open(...) will raise a RuntimeError if the password is bad
                            with trial_zf.open(zi, "r") as stream:
                                # read just a small chunk:
                                _ = stream.read(1024)
                        good_password = pwd
                        break
                    except RuntimeError:
                        # wrong password; try next
                        continue

                # Once we’ve tested the first encrypted file, no need to try others:
                break

    return {"files": files_info, "good_password": good_password}
