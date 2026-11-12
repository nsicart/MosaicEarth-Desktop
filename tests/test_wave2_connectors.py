import hashlib
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLERS = ROOT / "installers"


class Wave2OpenSourceConnectorsUnitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="me_desktop_wave2_"))
        cls.extracted = cls.tmp / "extracted"
        cls.extracted.mkdir(parents=True, exist_ok=True)
        for target in ("djiterra", "pix4d", "arcgispro", "postshot"):
            zpath = INSTALLERS / f"MosaicEarth-{target}-0.4.0.zip"
            assert zpath.is_file(), f"Missing built archive: {zpath}"
            out_dir = cls.extracted / target
            with zipfile.ZipFile(zpath, "r") as zf:
                zf.extractall(out_dir)
            sys.path.insert(0, str(out_dir))

    def test_01_deterministic_zip_checksums_match_manifest(self):
        sums_file = INSTALLERS / "SHA256SUMS.connectors"
        self.assertTrue(sums_file.is_file())
        entries = {}
        for line in sums_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                sha, name = line.strip().split()
                entries[name] = sha
        self.assertEqual(len(entries), 12)
        for name, expected_sha in entries.items():
            zp = INSTALLERS / name
            self.assertTrue(zp.is_file(), f"Missing {name}")
            actual = hashlib.sha256(zp.read_bytes()).hexdigest()
            self.assertEqual(actual, expected_sha, f"Checksum mismatch for {name}")

    def test_02_djiterra_hierarchy_staging(self):
        from mosaicearth_djiterra.connector import inspect_djiterra_project, stage_djiterra_folder

        mission = self.tmp / "Terra_Mission_20261112"
        (mission / "map").mkdir(parents=True)
        (mission / "models" / "pc" / "0" / "terra_b3dm").mkdir(parents=True)
        (mission / "models" / "pc" / "0" / "terra_las").mkdir(parents=True)
        (mission / "models" / "pc" / "0" / "terra_obj").mkdir(parents=True)
        (mission / "report").mkdir(parents=True)

        (mission / "map" / "result.tif").write_bytes(b"II*\x00ortho")
        (mission / "map" / "dsm.tif").write_bytes(b"II*\x00dsm")
        (mission / "models" / "pc" / "0" / "metadata.xml").write_text(
            "<ModelMetadata><SRS>EPSG:4326</SRS><SRSOrigin>2.1734,41.3851,45.0</SRSOrigin></ModelMetadata>",
            encoding="utf-8",
        )
        (mission / "models" / "pc" / "0" / "terra_b3dm" / "tileset.json").write_text('{"asset":{"version":"1.0"}}', encoding="utf-8")
        (mission / "models" / "pc" / "0" / "terra_b3dm" / "tile_0.b3dm").write_bytes(b"b3dm_payload")
        (mission / "models" / "pc" / "0" / "terra_las" / "cloud.laz").write_bytes(b"LASF_payload")
        (mission / "models" / "pc" / "0" / "terra_obj" / "block.obj").write_text("v 0 0 0\n", encoding="utf-8")
        (mission / "models" / "pc" / "0" / "terra_obj" / "block.mtl").write_text("newmtl m1\n", encoding="utf-8")
        (mission / "report" / "quality_report.pdf").write_bytes(b"%PDF-1.4\n%terra")

        plan = inspect_djiterra_project(mission)
        self.assertEqual(plan["inferred_date"], "2026-11-12")
        self.assertEqual(plan["deliverables_count"], 5)
        self.assertAlmostEqual(plan["metadata"]["lat"], 41.3851, places=4)

        manifest, paths = stage_djiterra_folder(self.tmp / "stage_terra", mission, "DJI Terra Quarry", target_project_id=77)
        self.assertEqual(manifest["metadata"]["connector"], "djiterra")
        self.assertEqual(manifest["metadata"]["survey_date"], "2026-11-12")
        classes = manifest["metadata"]["file_classes"]
        self.assertEqual(classes["products/result.tif"], "orthophoto")
        self.assertEqual(classes["products/dsm.tif"], "elevation")
        self.assertIn("products/terra_b3dm_3dtiles.zip", classes)
        self.assertIn("products/block.obj.zip", classes)
        self.assertIn("products/cloud.laz", classes)
        self.assertIn("attachments/quality_report.pdf", paths)

    def test_03_pix4dmapper_and_pix4dmatic_staging(self):
        from mosaicearth_pix4d.connector import inspect_pix4d_project, stage_pix4d_folder

        p4d = self.tmp / "Pix4D_Site_2026_11_10"
        (p4d / "1_initial" / "report").mkdir(parents=True)
        (p4d / "2_densification" / "point_cloud").mkdir(parents=True)
        (p4d / "2_densification" / "3d_mesh").mkdir(parents=True)
        (p4d / "3_dsm_ortho" / "1_dsm").mkdir(parents=True)
        (p4d / "3_dsm_ortho" / "2_mosaic" / "tiles").mkdir(parents=True)
        (p4d / "3_dsm_ortho" / "extras" / "contours").mkdir(parents=True)

        (p4d / "site.p4d").write_text("<pix4d/>", encoding="utf-8")
        (p4d / "1_initial" / "report" / "site_report.pdf").write_bytes(b"%PDF-1.4\n%pix4d")
        (p4d / "2_densification" / "point_cloud" / "site_group1_densified_point_cloud.laz").write_bytes(b"LASF")
        (p4d / "2_densification" / "3d_mesh" / "site_simplified_3d_mesh.obj").write_text("v 0 0 0\n", encoding="utf-8")
        (p4d / "3_dsm_ortho" / "1_dsm" / "site_dsm.tif").write_bytes(b"II*\x00dsm")
        (p4d / "3_dsm_ortho" / "2_mosaic" / "site_transparent_mosaic_group1.tif").write_bytes(b"II*\x00ortho")
        # Internal tile pyramid must be ignored
        (p4d / "3_dsm_ortho" / "2_mosaic" / "tiles" / "tile_0_0.tif").write_bytes(b"II*\x00tile")
        (p4d / "3_dsm_ortho" / "extras" / "contours" / "contours.shp").write_bytes(b"\x00\x00'\n")
        (p4d / "3_dsm_ortho" / "extras" / "contours" / "contours.shx").write_bytes(b"\x00\x00'\n")
        (p4d / "3_dsm_ortho" / "extras" / "contours" / "contours.dbf").write_bytes(b"\x03")

        plan = inspect_pix4d_project(p4d)
        self.assertEqual(plan["edition"], "pix4dmapper")
        self.assertEqual(len(plan["orthophotos"]), 1)
        self.assertEqual(len(plan["elevations"]), 1)
        self.assertEqual(len(plan["shapefiles"]), 1)

        manifest, paths = stage_pix4d_folder(self.tmp / "stage_p4d", p4d, "PIX4D Site")
        self.assertEqual(manifest["metadata"]["connector"], "pix4dmapper")
        classes = manifest["metadata"]["file_classes"]
        self.assertEqual(classes["products/site_dsm.tif"], "elevation")
        self.assertEqual(classes["products/site_transparent_mosaic_group1.tif"], "orthophoto")
        self.assertEqual(classes["products/contours.shp.zip"], "vector")
        self.assertIn("attachments/site_report.pdf", paths)

    def test_04_arcgispro_toolbox_and_staging(self):
        import importlib.machinery
        import importlib.util

        pyt_path = self.extracted / "arcgispro" / "MosaicEarth.pyt"
        self.assertTrue(pyt_path.is_file())
        loader = importlib.machinery.SourceFileLoader("mosaicearth_pyt_test", str(pyt_path))
        spec = importlib.util.spec_from_loader("mosaicearth_pyt_test", loader)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        tbx = mod.Toolbox()
        self.assertEqual(len(tbx.tools), 3)
        for tool_cls in tbx.tools:
            inst = tool_cls()
            params = inst.getParameterInfo()
            self.assertGreaterEqual(len(params), 5)

        from mosaicearth_arcgispro.connector import stage_arcgis_deliverables

        ag_dir = self.tmp / "arcgis_exports"
        ag_dir.mkdir(parents=True)
        (ag_dir / "breaklines.dxf").write_text("0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n", encoding="utf-8")
        (ag_dir / "parcel_dsm.tif").write_bytes(b"II*\x00dsm")
        manifest, _ = stage_arcgis_deliverables(self.tmp / "stage_ag", [ag_dir], "ArcGIS Parcel", survey_date="2026-11-12")
        self.assertEqual(manifest["metadata"]["connector"], "arcgispro")
        self.assertEqual(manifest["metadata"]["file_classes"]["products/breaklines.dxf"], "vector")
        self.assertEqual(manifest["metadata"]["file_classes"]["products/parcel_dsm.tif"], "elevation")

    def test_05_postshot_lichtfeld_auto_exif_gps_and_splat_validation(self):
        from mosaicearth_postshot.connector import SyncError, extract_georef_from_companion_inputs, stage_postshot_splat

        ps_dir = self.tmp / "postshot_scene"
        (ps_dir / "images").mkdir(parents=True)
        # Simulated DJI drone JPEG with XMP GPS attributes
        dji_jpeg = (
            b"\xff\xd8\xff\xe1\x00\x90http://ns.adobe.com/xap/1.0/\x00"
            b'<rdf:Description drone-dji:GpsLatitude="41.608210" drone-dji:GpsLongitude="2.287450" '
            b'drone-dji:AbsoluteAltitude="184.5" drone-dji:FlightYawDegree="92.0"/>\xff\xd9'
        )
        (ps_dir / "images" / "DJI_0001.JPG").write_bytes(dji_jpeg)

        geo = extract_georef_from_companion_inputs(ps_dir)
        self.assertIsNotNone(geo)
        self.assertAlmostEqual(geo["lat"], 41.60821, places=5)
        self.assertAlmostEqual(geo["lon"], 2.28745, places=5)
        self.assertAlmostEqual(geo["alt"], 184.5, places=1)

        # Untrained plain PLY without f_dc_0 must be rejected cleanly
        bad_ply = ps_dir / "untrained_cloud.ply"
        bad_ply.write_bytes(b"ply\nformat ascii 1.0\nelement vertex 1\nproperty float x\nend_header\n0\n")
        with self.assertRaises(SyncError):
            stage_postshot_splat(self.tmp / "stage_bad", bad_ply)

        # Trained Postshot 3DGS PLY
        good_ply = ps_dir / "cathedral_splat.ply"
        good_ply.write_bytes(
            b"ply\nformat binary_little_endian 1.0\nelement vertex 1\nproperty float x\nproperty float f_dc_0\nend_header\n\x00\x00\x00\x00\x00\x00\x00\x00"
        )
        manifest, _ = stage_postshot_splat(
            self.tmp / "stage_good",
            good_ply,
            "Cathedral 3DGS",
            studio_tool="lichtfeld",
            survey_date="2026-11-12",
        )
        self.assertEqual(manifest["metadata"]["connector"], "lichtfeld")
        self.assertAlmostEqual(manifest["metadata"]["georef"]["lat"], 41.60821, places=5)
        self.assertTrue(manifest["files"][0]["gaussian"])
        self.assertEqual(manifest["files"][0]["asset_class"], "gaussian")


if __name__ == "__main__":
    unittest.main()
