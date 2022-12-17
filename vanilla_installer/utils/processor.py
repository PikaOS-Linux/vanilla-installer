# processor.py
#
# Copyright 2022 mirkobrombin
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundationat version 3 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import os
import shutil
import logging
import tempfile
import subprocess
from glob import glob


logger = logging.getLogger("Installer::Processor")


class Processor:

    @staticmethod
    def gen_swap_size():
        """
        Reference: https://access.redhat.com/documentation/en-us/red_hat_enterprise_linux/7/html/storage_administration_guide/ch-swapspace#doc-wrapper
        """
        mem = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        mem = mem / (1024.0 ** 3)
        if mem <= 2:
            return int(mem * 3 * 1024)
        elif mem > 2 and mem <= 8:
            return int(mem * 2 * 1024)
        elif mem > 8 and mem <= 64:
            return int(mem * 1.5 * 1024)
        else:
            return 4096

    @staticmethod
    def gen_install_script(log_path, pre_run, post_run, finals):
        logger.info("processing the following final data: %s", finals)

        #manifest_remove = "/cdrom/casper/filesystem.manifest-remove"
        #if not os.path.exists(manifest_remove):
        manifest_remove = "/tmp/filesystem.manifest-remove"
        with open(manifest_remove, "w") as f:
            f.write("vanilla-installer\n")
            f.write("gparted\n")

        arguments = [
            "sudo", "distinst",
            "-s", "'/cdrom/casper/filesystem.squashfs'",
            "-r", f"'{manifest_remove}'",
            "-h", "'pika'",
        ]


        for final in finals:
            for key, value in final.items():
                if key == "users":
                    arguments = ["echo", f"'{value['password']}'", "|"] + arguments
                    arguments += ["--username", f"'{value['username']}'"]
                    arguments += ["--realname", f"'{value['fullname']}'"]
                    arguments += ["--profile_icon", "'/usr/share/pixmaps/faces/yellow-rose.jpg'"]
                elif key == "timezone":
                    arguments += ["--tz", "'{}/{}'".format(value["region"], value["zone"])]
                elif key == "language":
                    arguments += ["-l", f"'{value}'"]
                elif key == "keyboard":
                    arguments += ["-k", f"'{value}'"]
                elif key == "disk":
                    if "auto" in value:
                        arguments += ["-b", f"'{value['auto']['disk']}'"]
                        arguments += ["-t", "'{}:gpt'".format(value["auto"]["disk"])]
                        arguments += ["-n", "'{}:primary:start:1024M:fat32:mount=/boot/efi:flags=esp'".format(value["auto"]["disk"])]
                        arguments += ["-n", "'{}:primary:1024M:2048M:ext4:mount=/boot'".format(value["auto"]["disk"])]
                        arguments += ["-n", "'{}:primary:29000M:end:btrfs:mount=/'".format(value["auto"]["disk"])]
                        #arguments += ["-n", "'{}:primary:43008M:end:btrfs:mount=/home'".format(value["auto"]["disk"])]
                        #arguments += ["-n", "'{}:primary:-{}M:end:swap'".format(value["auto"]["disk"], Processor.gen_swap_size())]
                    else:
                        for partition, values in value.items():
                            if partition == "disk":
                                arguments += ["-b", f"'{values}'"]
                                arguments += ["-t", "'{}:gpt'".format(values)]
                                continue
                            if values["mp"] == "/":
                                arguments += ["-n", "'{}:primary:start:{}M:btrfs:mount=/'".format(partition, values["size"])]
                            elif values["mp"] == "/boot/efi":
                                arguments += ["-n", "'{}:primary:start:512M:fat32:mount=/boot/efi:flags=esp'".format(partition)]
                            elif values["mp"] == "swap":
                                arguments += ["-n", "'{}:primary:{}M:end:swap'".format(partition, values["size"])]
                            else:
                                arguments += ["-n", "'{}:primary:{}M:end:{}:mount={}'".format(partition, values["size"], values["fs"], values["mp"])]
        
        # generating a temporary file to store the distinst command and
        # arguments parsed from the final data
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
            f.write("#!/bin/sh\n")
            f.write("# This file was created by the Vanilla Installer.\n")
            f.write("# Do not edit this file manually!\n\n")


            f.write("set -e -x\n\n")

            if "VANILLA_FAKE" in os.environ:
                logger.info("VANILLA_FAKE is set, skipping the installation process.")
                f.write("echo 'VANILLA_FAKE is set, skipping the installation process.'\n")
                f.write("echo 'Printing the configuration instead:'\n")
                f.write("echo '----------------------------------'\n")
                f.write('echo "{}"\n'.format(finals))
                f.write("echo '----------------------------------'\n")
                f.write("sleep 5\n")
                f.write("exit 1\n")
            else:   
                for arg in arguments:
                    f.write(arg + " ")


            f.flush()
            f.close()

            # setting the file executable
            os.chmod(f.name, 0o755)
                
            return f.name
    
    @staticmethod
    def find_partitions(block_device, mountpoint, size, expected):
        logger.info("finding partitions for block device '{}' with mountpoint '{}' and size '{}'".format(block_device, mountpoint, size))
        partitions = []

        if block_device.startswith("/dev/"):
            block_device = block_device[5:]

        for partition in glob("/sys/block/{}/{}*".format(block_device, block_device)):
            partition_size = int(open(partition + "/size").read().strip()) * 512

            if partition_size == size:
                _part = "/dev/" + partition.split("/")[-1]
                _res = subprocess.check_output(["df", _part]).decode("utf-8").split("\n")[1].split()
                _used = int(_res[2])
                partitions.append((_part, _used))

        if len(partitions) < expected:
            raise Exception("not enough partitions found for block device '{}' with mountpoint '{}' and size '{}'".format(block_device, mountpoint, size))
        elif len(partitions) > expected:
            raise Exception("too many partitions found for block device '{}' with mountpoint '{}' and size '{}'".format(block_device, mountpoint, size))

        _partitions = sorted(partitions, key=lambda x: x[1], reverse=True)
        return [x[0] for x in _partitions]
    
    @staticmethod
    def find_partitions_by_fs(block_device, mountpoint, fs, expected):
        logger.info("finding partitions for block device '{}' with mountpoint '{}' and filesystem '{}'".format(block_device, mountpoint, fs))
        partitions = []

        if block_device.startswith("/dev/"):
            block_device = block_device[5:]

        for partition in glob("/sys/block/{}/{}*".format(block_device, block_device)):
            partition_fs = subprocess.check_output(["lsblk", "-no", "FSTYPE", "/dev/" + partition.split("/")[-1]]).decode("utf-8").strip()

            if partition_fs == fs:
                partitions.append("/dev/" + partition.split("/")[-1])

        if len(partitions) < expected:
            raise Exception("not enough partitions found for block device '{}' with mountpoint '{}' and filesystem '{}'".format(block_device, mountpoint, fs))
        elif len(partitions) > expected:
            raise Exception("too many partitions found for block device '{}' with mountpoint '{}' and filesystem '{}'".format(block_device, mountpoint, fs))

        return partitions
    
    @staticmethod
    def get_uuid(partition):
        logger.info("getting UUID for partition '{}'".format(partition))
        return subprocess.check_output(["lsblk", "-no", "UUID", partition]).decode("utf-8").strip()
    
    @staticmethod
    def label_partition(partition, label, fs=None):
        logger.info("labeling partition '{}' with label '{}'".format(partition, label))

        if fs is None:
            fs = subprocess.check_output(["lsblk", "-no", "FSTYPE", partition]).decode("utf-8").strip()

        if fs == "btrfs":
            subprocess.check_call(["sudo", "btrfs", "filesystem", "label", partition, label])
        elif fs == "ext4":
            subprocess.check_call(["sudo", "e2label", partition, label])
        elif fs == "vfat":
            subprocess.check_call(["sudo", "fatlabel", partition, label])
        else:
            raise Exception("unknown filesystem '{}'".format(fs))

        return True
    
    @staticmethod
    def umount_if(mountpoint):
        logger.info("unmounting '{}' if mounted".format(mountpoint))
        
        if os.path.ismount(mountpoint):
            subprocess.check_call(["sudo", "umount", "-l", mountpoint])

    @staticmethod
    def remove_uuid_from_fstab(root, uuid):
        logger.info("removing UUID '{}' from fstab".format(uuid))
        subprocess.check_call(["sudo", "sed", "-i", "/UUID={}/d".format(uuid), root + "/etc/fstab"])

    @staticmethod
    def update_grub(root, block_device):
        logger.info("updating GRUB in '{}'".format(root))
        boot_partition = Processor.find_partitions_by_fs(block_device, "/boot", "ext4", 1)[0]
        efi_partition = Processor.find_partitions_by_fs(block_device, "/boot/efi", "vfat", 1)[0]

        Processor.umount_if(boot_partition)
        Processor.umount_if(efi_partition)

        subprocess.check_call(["sudo", "mount", boot_partition, root + "/boot"])
        subprocess.check_call(["sudo", "mount", efi_partition, root + "/boot/efi"])
        subprocess.check_call(["sudo", "mount", "--bind", "/dev", root + "/dev"])
        subprocess.check_call(["sudo", "mount", "--bind", "/dev/pts", root + "/dev/pts"])
        subprocess.check_call(["sudo", "mount", "--bind", "/proc", root + "/proc"])
        subprocess.check_call(["sudo", "mount", "--bind", "/sys", root + "/sys"])
        subprocess.check_call(["sudo", "mount", "--bind", "/run", root + "/run"])

        script = [
            "#!/bin/bash",
            "sudo chroot {} grub-mkconfig -o /boot/grub/grub.cfg".format(root),
        ]
        subprocess.check_call("\n".join(script), shell=True)
        
        subprocess.check_call(["sudo", "grub-install", "--boot-directory", root + "/boot", "--target=x86_64-efi", block_device])
        script = [ # for some reason, grub-install doesn't work if we don't install it from the chroot too
            "#!/bin/bash",
            "sudo chroot {} grub-install --boot-directory /boot {} --target=x86_64-efi".format(root, block_device),
        ]
        subprocess.check_call("\n".join(script), shell=True)

        Processor.umount_if(boot_partition)
        Processor.umount_if(efi_partition)
        return True
        
