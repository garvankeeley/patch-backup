# HG changeset patch
# User Garvan Keeley <gkeeley@mozilla.com>
# Parent  d30e6fbe467a4788c01637d184d288f4c5686345
Bug 1063329 - Part 3, add xcode_packend.py, the project generator

diff --git a/python/mozbuild/mozbuild/backend/xcode_backend.py b/python/mozbuild/mozbuild/backend/xcode_backend.py
new file mode 100644
--- /dev/null
+++ b/python/mozbuild/mozbuild/backend/xcode_backend.py
@@ -0,0 +1,150 @@
+# This Source Code Form is subject to the terms of the Mozilla Public
+# License, v. 2.0. If a copy of the MPL was not distributed with this
+# file, You can obtain one at http://mozilla.org/MPL/2.0/.
+
+import os
+import types
+import shutil
+import json
+import re
+from .common import CommonBackend
+from mod_pbxproj.mod_pbxproj import XcodeProject, PBXFileReference, PBXBuildFile
+# from ..compilation.database import CompilationDatabase
+
+
+class XcodeBackend(CommonBackend):
+    """Backend that generates XCode project files.
+    """
+
+    def _init(self):
+        def detailed(summary):
+            return ('Generates XCode project. Files will compile, predictively show errors, and code-complete.'
+                    ' Actually building Firefox still requires command-line mach.')
+
+        self.summary.backend_detailed_summary = types.MethodType(detailed, self.summary)
+
+        #c = CompilationDatabase()
+        #c.compile_db()
+
+        self._topobjdir = self.environment.topobjdir
+        self._topsrcdir = self.environment.topsrcdir
+        self._xcode_groups = {}
+
+        CommonBackend._init(self)
+
+        self._init_xcode_project()
+
+        self._get_all_headers(self._topsrcdir)
+
+        with open(os.path.join(self._topobjdir, 'compile_commands.json')) as f:
+            data = json.load(f)
+            for item in data:
+                self._process_compile_command(item['file'], item['command'])
+
+    def consume_object(self, obj):
+        obj.ack()
+        return
+
+    def consume_finished(self):
+        output_xcconfig = os.path.join(self._topobjdir, 'config.xcconfig')
+        shutil.copyfile(os.path.join(self._current_path, 'templates/xcode/stub_xcconfig'), output_xcconfig)
+        self._xcode_project.save(sort=True)
+        print 'Xcode file is at: ' + self._xcode_project.pbxproj_path
+
+    def _init_xcode_project(self):
+        xcode_proj_path = os.path.join(self._topobjdir, 'firefox.xcodeproj/project.pbxproj')
+        try:
+            os.mkdir(os.path.dirname(xcode_proj_path))
+        except OSError:
+            pass
+
+        self._current_path = os.path.dirname(os.path.realpath(__file__))
+
+        shutil.copy(os.path.join(self._current_path, 'templates/xcode/stub_project.pbxproj'),
+                    xcode_proj_path)
+
+        xcshared_file = os.path.dirname(xcode_proj_path) + "/xcshareddata/xcschemes/firefox.xcscheme"
+        if not os.path.exists(os.path.dirname(xcshared_file)):
+            os.makedirs(os.path.dirname(xcshared_file))
+        shutil.copy(os.path.join(self._current_path, 'templates/xcode/stub_firefox.xcscheme'),
+                    xcshared_file)
+        with open(xcshared_file) as in_file:
+            text = in_file.read()
+        with open(xcshared_file, 'w') as out_file:
+            out_file.write(text.replace('REPLACE_ME_WITH_PATH', self._topobjdir))
+
+        self._xcode_project = XcodeProject.Load(xcode_proj_path)
+        PBXFileReference.types['.asm'] = ('sourcecode.nasm', 'PBXSourcesBuildPhase')
+        PBXFileReference.types['.cc'] = ('sourcecode.cpp.cpp', 'PBXSourcesBuildPhase')
+        PBXFileReference.types['.cxx'] = ('sourcecode.cpp.cpp', 'PBXSourcesBuildPhase')
+
+    def _get_files_from_unified(self, unified_file_full_path, group_full_path, group):
+        with open(unified_file_full_path) as f:
+            for line in f:
+                match = re.search(r'include\s+"(.+)"', line)
+                if match:
+                    cpp = match.group(1)
+                    cpp_full = self._topsrcdir + os.path.join(group_full_path, cpp)
+                    self._add_file_to_xcode_group(group,
+                                                  cpp_full,
+                                                  is_built=False, flags=None)
+
+    def _process_compile_command(self, full_file_path, command):
+        module = os.path.dirname(full_file_path)
+        module = module.replace(self._topobjdir, '')
+        module = module.replace(self._topsrcdir, '')
+        command = command.rsplit(' ', 1)[0]
+        command = command[command.index('-c ') + 3:]
+
+        group = self._add_group_to_xcode(self._topsrcdir, module, '')
+        self._add_file_to_xcode_group(group, full_file_path, is_built=True, flags=command)
+
+        if 'Unified_cpp' in full_file_path:
+            self._get_files_from_unified(full_file_path,
+                                         os.path.join(self._topsrcdir, module),
+                                         group)
+
+    def _get_or_create_xcode_group(self, abs_src_path, sub_path, depth=0):
+        if not sub_path or len(sub_path) < 2:
+            return None
+
+        path = os.path.join(abs_src_path, sub_path)
+
+        if path in self._xcode_groups:
+            return self._xcode_groups[path]
+
+        parent_group = None
+        if '/' in sub_path:
+            parent_group = self._get_or_create_xcode_group(abs_src_path, os.path.dirname(sub_path), depth + 1)
+
+        leaf_dir_name = os.path.basename(sub_path)
+        group = self._xcode_project.get_or_create_group(leaf_dir_name, path, parent_group)
+        self._xcode_groups[path] = group
+        return group
+
+    def _add_group_to_xcode(self, abs_path_to_srcs, sub_path, file):
+        if '/' in file:
+            folder = os.path.dirname(file)
+            sub_path = os.path.join(sub_path, folder)
+
+        group = self._get_or_create_xcode_group(abs_path_to_srcs, sub_path)
+        return group
+
+    def _add_file_to_xcode_group(self, group, full_path_to_file, is_built, flags):
+        result = self._xcode_project.add_file(full_path_to_file, group,
+                                              create_build_files=is_built, ignore_unknown_type=True)
+        if len(result) == 0:
+            return
+
+        for item in result:
+            if isinstance(item, PBXBuildFile) and flags:
+                item.add_compiler_flag(flags)
+
+    def _get_all_headers(self, directory):
+        for root, dirs, files in os.walk(directory):
+            dirs[:] = [d for d in dirs if not d.startswith('.')]
+            for file in files:
+                if file.endswith('.h'):
+                    module = root.replace(directory + '/', '')
+                    group = self._add_group_to_xcode(self._topsrcdir, module, '')
+                    self._add_file_to_xcode_group(group, os.path.join(root, file), is_built=False, flags=None)
