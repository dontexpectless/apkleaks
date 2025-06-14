#!/usr/bin/env python3
import io
import json
import logging.config
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import subprocess

from contextlib import closing
from shutil import which
from pathlib import Path
from shlex import quote
from urllib.request import urlopen
from zipfile import ZipFile

from pyaxmlparser import APK

from apkleaks.colors import color as col
from apkleaks.utils import util

class APKLeaks:
	def __init__(self, args):
		self.apk = None
		self.file = os.path.realpath(args.file)
		self.json = args.json
		self.disarg = args.args
		self.prefix = "apkleaks-"
		self.tempdir = tempfile.mkdtemp(prefix=self.prefix)
		self.main_dir = os.path.dirname(os.path.realpath(__file__))
		self.output = tempfile.mkstemp(suffix=".%s" % ("json" if self.json else "txt"), prefix=self.prefix)[1] if args.output is None else args.output
		self.fileout = open(self.output, "%s" % ("w" if self.json else "a"))
		self.pattern = os.path.join(str(Path(self.main_dir).parent), "config", "regexes.json") if args.pattern is None else args.pattern
		self.jadx = which("jadx") if which("jadx") is not None else os.path.join(str(Path(self.main_dir).parent), "jadx", "bin", "jadx%s" % (".bat" if os.name == "nt" else "")).replace("\\","/")
		self.out_json = {}
		self.scanned = False
		logging.config.dictConfig({"version": 1, "disable_existing_loggers": True})

	def apk_info(self):
		return APK(self.file)

	def dependencies(self):
		exter = "https://github.com/skylot/jadx/releases/download/v1.2.0/jadx-1.2.0.zip"
		try:
			with closing(urlopen(exter)) as jadx:
				with ZipFile(io.BytesIO(jadx.read())) as zfile:
					zfile.extractall(os.path.join(str(Path(self.main_dir).parent), "jadx"))
			os.chmod(self.jadx, 33268)
		except Exception as error:
			util.writeln(str(error), col.WARNING)
			sys.exit()

	def integrity(self):
		if os.path.exists(self.jadx) is False:
			util.writeln("Can't find jadx binary.", col.WARNING)
			valid = {"yes": True, "y": True, "ye": True, "no": False, "n": False}
			while True:
				util.write("Do you want to download jadx? (Y/n) ", col.OKBLUE)
				try:
					choice = input().lower()
					if choice == "":
						choice = valid["y"]
						break
					elif choice in valid:
						choice = valid[choice]
						break
					else:
						util.writeln("\nPlease respond with 'yes' or 'no' (or 'y' or 'n').", col.WARNING)
				except KeyboardInterrupt:
					sys.exit(util.writeln("\n** Interrupted. Aborting.", col.FAIL))
			if choice:
				util.writeln("\n** Downloading jadx...\n", col.OKBLUE)
				self.dependencies()
			else:
				sys.exit(util.writeln("\n** Aborted.", col.FAIL))
		if os.path.isfile(self.file):
			try:
				self.apk = self.apk_info()
			except Exception as error:
				util.writeln(str(error), col.WARNING)
				sys.exit()
			else:
				return self.apk
		else:
			sys.exit(util.writeln("It's not a valid file!", col.WARNING))

	def decompile(self):
		util.writeln("** Decompiling APK...", col.OKBLUE)
		args = [self.jadx, self.file, "-d", self.tempdir]
		try:
			args.extend(re.split(r"\\s|=", self.disarg))
		except Exception:
			pass
		# Use Popen to capture stderr
		try:
			process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
			stdout, stderr = process.communicate()

			# Print stderr and write to output file
			if stderr:
				util.writeln("** Jadx stderr output:", col.WARNING)
				print(stderr) # Print to console
				self.fileout.write("\n** Jadx stderr output:\n")
				self.fileout.write(stderr)

			if process.returncode != 0:
				util.writeln(f"Error running jadx: Command '{' '.join(args)}' returned non-zero exit status {process.returncode}.", col.WARNING)
				# Note: The exception handling below might still be triggered

		except Exception as e:
			util.writeln(f"Error running jadx: {e}", col.WARNING)

	def extract(self, name, matches):
		if len(matches):
			stdout = ("[%s]" % (name))
			util.writeln("\n" + stdout, col.OKGREEN)
			self.fileout.write("%s" % (stdout + "\n" if self.json is False else ""))
			for secret in matches:
				if name == "LinkFinder":
					if re.match(r"^.(L[a-z]|application|audio|fonts|image|kotlin|layout|multipart|plain|text|video).*\/.+", secret) is not None:
						continue
					secret = secret[len("'"):-len("'")]
				stdout = ("- %s" % (secret))
				print(stdout)
				self.fileout.write("%s" % (stdout + "\n" if self.json is False else ""))
			self.fileout.write("%s" % ("\n" if self.json is False else ""))
			self.out_json["results"].append({"name": name, "matches": matches})
			self.scanned = True

	def scanning(self):
		if self.apk is None:
			sys.exit(util.writeln("** Undefined package. Exit!", col.FAIL))
		util.writeln("\n** Scanning against '%s'" % (self.apk.package), col.OKBLUE)
		self.out_json["package"] = self.apk.package
		self.out_json["results"] = []
		with open(self.pattern) as regexes:
			regex = json.load(regexes)
			for name, pattern in regex.items():
				if isinstance(pattern, list):
					for p in pattern:
						try:
							thread = threading.Thread(target = self.extract, args = (name, util.finder(p, self.tempdir)))
							thread.start()
						except KeyboardInterrupt:
							sys.exit(util.writeln("\n** Interrupted. Aborting...", col.FAIL))
				else:
					try:
						thread = threading.Thread(target = self.extract, args = (name, util.finder(pattern, self.tempdir)))
						thread.start()
					except KeyboardInterrupt:
						sys.exit(util.writeln("\n** Interrupted. Aborting...", col.FAIL))

	def copy_decompiled_files(self):
		"""Copies decompiled files from tempdir to a new directory near the original APK."""
		try:
			# Get the directory of the input APK file
			# apk_dir = os.path.dirname(self.file)
			# Get the current working directory where the script is being run
			current_dir = os.getcwd()

			# Get the base name of the input APK file without extension
			apk_basename = os.path.basename(self.file)
			apk_name_without_ext = os.path.splitext(apk_basename)[0]

			# Define the new directory name
			# Using '-extracted' as it describes the content well
			dest_dir_name = f"{apk_name_without_ext}-extracted"
			dest_path = os.path.join(current_dir, dest_dir_name)

			util.writeln(f"\n** Copying decompiled files to '{dest_path}'...", col.OKBLUE)

			# Ensure the destination directory exists and is empty or create it
			if os.path.exists(dest_path):
				if os.listdir(dest_path): # Check if directory is not empty
					util.writeln(f"Warning: Destination directory '{dest_path}' is not empty. Files might be overwritten or merged.", col.WARNING)
				# shutil.rmtree(dest_path) # Option to clear it first
				# os.makedirs(dest_path) # Option to recreate it
			else:
				os.makedirs(dest_path)

			# Copy the contents of the temporary directory
			# copytree requires the destination to NOT exist, or use dirs_exist_ok=True (Python 3.8+)
			# Let's handle pre-creating the dir and using copytree carefully
			# A safer way for older Python versions is to copy contents item by item,
			# but shutil.copytree is more efficient.
			# Given the environment is Python 3.13, dirs_exist_ok should work.
			shutil.copytree(self.tempdir, dest_path, dirs_exist_ok=True)

			util.writeln(f"** Decompiled files copied successfully to '{dest_path}'.", col.OKGREEN)

		except Exception as e:
			util.writeln(f"Error copying decompiled files: {e}", col.WARNING)

	def cleanup(self):
		# Copy files before cleaning up tempdir
		self.copy_decompiled_files()
		shutil.rmtree(self.tempdir)
		if self.scanned:
			self.fileout.write("%s" % (json.dumps(self.out_json, indent=4) if self.json else ""))
			self.fileout.close()
			print("%s\n** Results saved into '%s%s%s%s'%s." % (col.HEADER, col.ENDC, col.OKGREEN, self.output, col.HEADER, col.ENDC))
		else:
			self.fileout.close()
			for _ in range(5):  # Intenta varias veces por si el sistema tarda en liberar el archivo
				try:
					os.remove(self.output)
					break
				except PermissionError:
					time.sleep(0.2)
			util.writeln("\n** Done with nothing. ¯\\_(ツ)_/¯", col.WARNING)
